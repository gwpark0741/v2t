from __future__ import annotations

from pathlib import Path

from .common import (
    load_stage_warnings,
    render_badge,
    render_summary_card,
    render_video_or_placeholder,
    render_warning_table,
    resolve_video_src,
    safe_text,
    stage_output,
)


def _is_unresolved(action: dict) -> bool:
    unknown_resolution = action.get("unknown_resolution")
    if not isinstance(unknown_resolution, dict):
        return False
    return unknown_resolution.get("suggestion") == "UNRESOLVED"


def _format_event(event: dict) -> str:
    if event.get("type") == "onset":
        timestamp = event.get("timestamp")
        return f"onset @ {float(timestamp):.3f}s" if timestamp is not None else "onset"
    start_time = event.get("start_time")
    end_time = event.get("end_time")
    if start_time is not None and end_time is not None:
        return f"continuous @ {float(start_time):.3f}s ~ {float(end_time):.3f}s"
    return safe_text(event.get("type"))


def render_tab(run_dir: Path, report_html_path: Path) -> str | None:
    payload = stage_output(run_dir, "stage_05_agent_b")
    if not isinstance(payload, dict):
        return None
    cut_outputs = payload.get("cut_outputs", [])
    if not isinstance(cut_outputs, list):
        return None

    stage04 = stage_output(run_dir, "stage_04_segment_prep")
    clip_by_cut_id = {}
    if isinstance(stage04, dict):
        for clip in stage04.get("clips", []):
            if isinstance(clip, dict):
                clip_by_cut_id[str(clip.get("cut_id", ""))] = clip

    summary = "".join(
        [
            render_summary_card("Total Actions", payload.get("total_actions", 0)),
            render_summary_card("UNRESOLVED", payload.get("unresolved_count", 0)),
            render_summary_card("Cuts", len(cut_outputs)),
            render_summary_card("Prompt Tokens", payload.get("aggregate_usage", {}).get("prompt_token_count", 0)),
            render_summary_card("Output Tokens", payload.get("aggregate_usage", {}).get("candidates_token_count", 0)),
            render_summary_card("Total Tokens", payload.get("aggregate_usage", {}).get("total_token_count", 0)),
            render_summary_card("Cost (USD)", f"{float(payload.get('estimated_total_cost_usd', 0.0)):.6f}"),
            render_summary_card("Model Latency (ms)", f"{float(payload.get('total_model_latency_ms', 0.0)):.2f}"),
        ]
    )

    items = []
    for index, cut_output in enumerate(cut_outputs, start=1):
        if not isinstance(cut_output, dict):
            continue
        cut_id = str(cut_output.get("cut_id", ""))
        actions = cut_output.get("actions", [])
        unresolved_count = sum(1 for action in actions if isinstance(action, dict) and _is_unresolved(action))
        accordion_id = f"agent-b-{index}"
        clip = clip_by_cut_id.get(cut_id, {})
        video_html = render_video_or_placeholder(
            resolve_video_src(clip.get("local_clip_path"), report_html_path) if isinstance(clip, dict) else "",
            controls=True,
            muted=True,
        )
        action_cards = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            event = action.get("event", {})
            unresolved_badge = render_badge("warning", kind="severity") if _is_unresolved(action) else ""
            action_cards.append(
                "<div class='action-card stack'>"
                "<div class='action-meta'>"
                f"<span class='mono truncate'>{safe_text(action.get('action_id'))}</span>"
                f"{render_badge(str(action.get('interaction_type', 'ambience')))}"
                f"{unresolved_badge}"
                "</div>"
                f"<div><span class='muted'>source</span> <span class='mono truncate'>{safe_text(action.get('primary_source_id'))}</span></div>"
                f"<div class='clamp-2 break-word'>{safe_text(action.get('sound_description'))}</div>"
                f"<div class='mono'>{safe_text(_format_event(event if isinstance(event, dict) else {}))}</div>"
                "</div>"
            )
        items.append(
            "<div class='accordion-item'>"
            f"<button class='accordion-trigger' type='button' data-accordion-target='{accordion_id}'>"
            "<div class='accordion-meta'>"
            f"<span class='mono truncate'>{safe_text(cut_id)}</span>"
            f"<span class='badge badge-info'>{len(actions)} actions</span>"
            f"{render_badge('warning', kind='severity') if unresolved_count else ''}"
            "</div>"
            f"<span class='muted'>{unresolved_count} unresolved</span>"
            "</button>"
            f"<div class='accordion-content' id='{accordion_id}'>"
            f"<div class='grid-2-agent-b'><div>{video_html}</div><div class='action-list'>{''.join(action_cards)}</div></div>"
            "</div>"
            "</div>"
        )

    warnings = render_warning_table(load_stage_warnings(run_dir, "stage_05_agent_b"))
    return f"<div class='summary-grid'>{summary}</div>{''.join(items)}{warnings}"
