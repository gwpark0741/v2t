from __future__ import annotations

import json

from math import ceil
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


def _to_percent(value: float, duration: float) -> float:
    if not duration or duration <= 0:
        return 0.0
    return max(0.0, min(100.0, (value / duration) * 100.0))


def _is_valid_event(event: dict, duration: float) -> bool:
    event_type = event.get("type")
    if event_type == "onset":
        timestamp = event.get("timestamp")
        return timestamp is not None and 0 <= float(timestamp) <= duration
    if event_type == "continuous":
        start_time = event.get("start_time")
        end_time = event.get("end_time")
        return (
            start_time is not None
            and end_time is not None
            and float(start_time) < float(end_time)
            and float(start_time) >= 0
            and float(end_time) <= duration * 1.05
        )
    return False


def _render_rule_lines(duration: float) -> str:
    if duration <= 0:
        return ""
    upper = int(ceil(duration))
    pieces: list[str] = []
    for second in range(0, upper + 1):
        left = _to_percent(float(second), duration)
        rule_class = "timeline-major-rule" if second % 5 == 0 else "timeline-rule"
        pieces.append(f"<div class='{rule_class}' style='left:{left:.4f}%'></div>")
        if second % 5 == 0:
            label_class = "timeline-major-label mono timeline-start-label" if second == 0 else "timeline-major-label mono"
            pieces.append(
                f"<div class='{label_class}' style='left:{left:.4f}%'>{second}s</div>"
            )
    return "".join(pieces)


def _render_lane_rules(duration: float) -> str:
    if duration <= 0:
        return ""
    upper = int(ceil(duration))
    return "".join(
        f"<div class='{'timeline-major-rule' if second % 5 == 0 else 'timeline-rule'}' style='left:{_to_percent(float(second), duration):.4f}%'></div>"
        for second in range(0, upper + 1)
    )


def _event_color_class(interaction_type: str) -> str:
    return f"interaction-color-{interaction_type}"


def _interaction_sort_key(interaction_type: str) -> tuple[int, str]:
    order = {
        "sfx": 0,
        "ambience": 1,
    }
    return (order.get(interaction_type, 99), interaction_type)


def render_tab(run_dir: Path, report_html_path: Path) -> str | None:
    payload = stage_output(run_dir, "stage_06_agent_c")
    if not isinstance(payload, dict):
        return None
    pipeline_result = payload.get("pipeline_result", {})
    if not isinstance(pipeline_result, dict):
        return None
    track_manifest = pipeline_result.get("track_manifest", {})
    tracks = track_manifest.get("tracks", []) if isinstance(track_manifest, dict) else []

    stage02 = stage_output(run_dir, "stage_02_full_video_asset")
    stage01 = stage_output(run_dir, "stage_01_local_preprocessing")
    video_src = ""
    duration = 0.0
    if isinstance(stage02, dict):
        local = stage02.get("local", {})
        if isinstance(local, dict):
            video_src = resolve_video_src(local.get("video_path"), report_html_path)
    if isinstance(stage01, dict):
        metadata = stage01.get("video_metadata", {})
        if isinstance(metadata, dict):
            duration = float(metadata.get("duration_seconds", 0.0))

    summary = "".join(
        [
            render_summary_card("Tracks", len(tracks)),
            render_summary_card("Unresolved", len(pipeline_result.get("unresolved_unknowns", []))),
            render_summary_card("Warnings", len(pipeline_result.get("warnings", []))),
            render_summary_card("LLM Calls", payload.get("llm_call_count", payload.get("flash_call_count", 0))),
            render_summary_card(
                "LLM Tokens",
                payload.get("llm_usage", payload.get("flash_usage", {})).get("total_token_count", 0),
            ),
            render_summary_card(
                "LLM Cost (USD)",
                f"{float(payload.get('estimated_llm_cost_usd', payload.get('estimated_flash_cost_usd', 0.0))):.6f}",
            ),
            render_summary_card(
                "LLM Latency (ms)",
                f"{float(payload.get('total_llm_latency_ms', payload.get('total_flash_latency_ms', 0.0))):.2f}",
            ),
        ]
    )

    video_id = "pipeline-report-source-video"
    timeline_rows = []
    lane_rules = _render_lane_rules(duration)
    sorted_tracks = sorted(
        (track for track in tracks if isinstance(track, dict)),
        key=lambda track: (
            _interaction_sort_key(str(track.get("interaction_type", "ambience"))),
            str(track.get("track_id", "")),
        ),
    )
    for track in sorted_tracks:
        interaction_type = str(track.get("interaction_type", "ambience"))
        events = track.get("events", [])
        event_html = []
        for event in events:
            if not isinstance(event, dict) or not _is_valid_event(event, duration):
                continue
            if event.get("type") == "onset":
                left = _to_percent(float(event.get("timestamp", 0.0)), duration)
                event_html.append(
                    f"<div class='timeline-event-onset {_event_color_class(interaction_type)}' "
                    f"style='left:{left:.4f}%' "
                    f"title=\"{safe_text(track.get('sound_description'))}\"></div>"
                )
            else:
                start_time = float(event.get("start_time", 0.0))
                end_time = float(event.get("end_time", 0.0))
                width = max(0.0, _to_percent(end_time, duration) - _to_percent(start_time, duration))
                if width <= 0:
                    continue
                event_html.append(
                    f"<div class='timeline-event-continuous {_event_color_class(interaction_type)}' "
                    f"style='left:{_to_percent(start_time, duration):.4f}%; width:max({width:.4f}%, 3px)' "
                    f"title=\"{safe_text(track.get('sound_description'))}\"></div>"
                )
        timeline_rows.append(
            "<div class='timeline-row'>"
            "<div class='timeline-label'>"
            f"<div class='mono break-word'>{safe_text(track.get('track_id'))}</div>"
            f"<div class='break-word' style='margin-top:6px; color: var(--text-secondary)'>{safe_text(track.get('sound_description'))}</div>"
            f"<div style='margin-top:4px'>{render_badge(interaction_type)}</div>"
            "</div>"
            f"<div class='timeline-lane'><div class='timeline-lane-inner'>{lane_rules}{''.join(event_html)}</div></div>"
            "</div>"
        )

    timeline = (
        f"<div class='timeline-wrap' data-video-id='{video_id}'>"
        "<div class='timeline-overlay'><div class='timeline-cursor'></div></div>"
        "<div class='timeline-ruler-row'>"
        "<div class='timeline-ruler-label'></div>"
        f"<div class='timeline-ruler-lane'><div class='timeline-ruler-inner'>{_render_rule_lines(duration)}</div></div>"
        "</div>"
        f"{''.join(timeline_rows) if timeline_rows else '<div class=\"empty-state\">No tracks available</div>'}"
        "</div>"
    )

    warnings = render_warning_table(load_stage_warnings(run_dir, "stage_06_agent_c"))
    judgments = payload.get("track_group_judgments")
    judgment_title = "Track Group Judgments"
    new_format = True
    if not isinstance(judgments, list):
        judgments = payload.get("surface_judgments", [])
        judgment_title = "Surface Judgments"
        new_format = False
    judgment_section = ""
    if isinstance(judgments, list):
        if judgments:
            if new_format:
                rows = "".join(
                    "<tr>"
                    f"<td class='mono truncate'>{safe_text(item.get('group_key'))}</td>"
                    f"<td class='mono truncate'>{safe_text(', '.join(item.get('input_action_ids', [])))}</td>"
                    f"<td class='mono'>{safe_text(item.get('source'))}</td>"
                    f"<td class='truncate'>{safe_text(item.get('model'))}</td>"
                    f"<td><pre>{safe_text(json.dumps(item.get('output_groups', []), indent=2, ensure_ascii=False))}</pre></td>"
                    "</tr>"
                    for item in judgments
                    if isinstance(item, dict)
                )
                heading = "<th>Group Key</th><th>Input Action IDs</th><th>Source</th><th>Model</th><th>Output Groups</th>"
            else:
                rows = "".join(
                    "<tr>"
                    f"<td class='mono truncate'>{safe_text(item.get('action_id_a'))}</td>"
                    f"<td class='mono truncate'>{safe_text(item.get('action_id_b'))}</td>"
                    f"<td>{render_badge(str(item.get('interaction_type', 'ambience')))}</td>"
                    f"<td class='mono truncate'>{safe_text(item.get('surface_context_a'))}</td>"
                    f"<td class='mono truncate'>{safe_text(item.get('surface_context_b'))}</td>"
                    f"<td class='mono'>{safe_text(item.get('result'))}</td>"
                    f"<td class='mono'>{safe_text(item.get('source'))}</td>"
                    f"<td class='truncate'>{safe_text(item.get('model'))}</td>"
                    f"<td><div class='clamp-2 break-word'>{safe_text(item.get('reason'))}</div></td>"
                    "</tr>"
                    for item in judgments
                    if isinstance(item, dict)
                )
                heading = "<th>Action A</th><th>Action B</th><th>Interaction</th><th>Surface A</th><th>Surface B</th><th>Result</th><th>Source</th><th>Model</th><th>Reason</th>"
            judgment_section = (
                "<div class='card' style='margin-top:16px'>"
                f"<div class='section-title'><h3>{safe_text(judgment_title)}</h3></div>"
                f"<div class='table-wrap'><table><thead><tr>{heading}</tr></thead>"
                f"<tbody>{rows}</tbody></table></div></div>"
            )
        else:
            judgment_section = (
                "<div class='card' style='margin-top:16px'>"
                f"<div class='section-title'><h3>{safe_text(judgment_title)}</h3></div>"
                f"<div class='empty-state'>No {safe_text(judgment_title).lower()} recorded.</div>"
                "</div>"
            )
    return (
        f"<div class='summary-grid'>{summary}</div>"
        f"<div class='video-frame'>{render_video_or_placeholder(video_src, controls=True, muted=True, element_id=video_id)}</div>"
        f"{timeline}"
        f"{judgment_section}"
        f"{warnings}"
    )
