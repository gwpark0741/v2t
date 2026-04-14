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


def _render_characters(characters: list[dict]) -> str:
    rows = "".join(
        "<tr>"
        f"<td class='mono truncate'>{safe_text(item.get('id'))}</td>"
        f"<td>{safe_text(item.get('label'))}</td>"
        f"<td>{render_badge(str(item.get('audibility', 'inactive')), kind='audibility')}</td>"
        f"<td><div class='break-word'>{safe_text(item.get('visual_description'))}</div></td>"
        "</tr>"
        for item in characters
    )
    return (
        "<div class='card'>"
        f"<div class='section-title'><h3>Characters</h3><span class='badge badge-info'>{len(characters)}</span></div>"
        "<div class='table-wrap'><table><colgroup>"
        "<col style='width:15%'><col style='width:20%'><col style='width:15%'><col style='width:50%'>"
        "</colgroup><thead><tr><th>ID</th><th>Label</th><th>Audibility</th><th>Visual Description</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></div>"
    )


def _render_key_objects(key_objects: list[dict]) -> str:
    rows = "".join(
        "<tr>"
        f"<td class='mono truncate'>{safe_text(item.get('id'))}</td>"
        f"<td>{safe_text(item.get('label'))}</td>"
        f"<td>{safe_text(item.get('material'))}</td>"
        f"<td>{safe_text(item.get('surface'))}</td>"
        f"<td>{render_badge(str(item.get('audibility', 'inactive')), kind='audibility')}</td>"
        f"<td class='mono'>{safe_text(item.get('has_mechanism'))}</td>"
        f"<td><div class='break-word'>{safe_text(item.get('visual_description'))}</div></td>"
        "</tr>"
        for item in key_objects
    )
    return (
        "<div class='card'>"
        f"<div class='section-title'><h3>KeyObjects</h3><span class='badge badge-info'>{len(key_objects)}</span></div>"
        "<div class='table-wrap'><table><colgroup>"
        "<col style='width:12%'><col style='width:18%'><col style='width:12%'><col style='width:12%'><col style='width:14%'><col style='width:10%'><col style='width:22%'>"
        "</colgroup><thead><tr><th>ID</th><th>Label</th><th>Material</th><th>Surface</th><th>Audibility</th><th>Mechanism</th><th>Visual Description</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></div>"
    )


def _render_ambience(ambience_sources: list[dict]) -> str:
    rows = "".join(
        "<tr>"
        f"<td class='mono truncate'>{safe_text(item.get('id'))}</td>"
        f"<td>{safe_text(item.get('label'))}</td>"
        f"<td><div class='break-word'>{safe_text(item.get('space_description'))}</div></td>"
        f"<td>{safe_text(item.get('distance_profile'))}</td>"
        f"<td><div class='break-word'>{safe_text(item.get('tonal_quality'))}</div></td>"
        "</tr>"
        for item in ambience_sources
    )
    return (
        "<div class='card'>"
        f"<div class='section-title'><h3>AmbienceSources</h3><span class='badge badge-info'>{len(ambience_sources)}</span></div>"
        "<div class='table-wrap'><table><colgroup>"
        "<col style='width:12%'><col style='width:18%'><col style='width:35%'><col style='width:15%'><col style='width:20%'>"
        "</colgroup><thead><tr><th>ID</th><th>Label</th><th>Space Description</th><th>Distance</th><th>Tonal Quality</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></div>"
    )


def render_tab(run_dir: Path, report_html_path: Path) -> str | None:
    payload = stage_output(run_dir, "stage_03_agent_a")
    if not isinstance(payload, dict):
        return None
    response = payload.get("response", {})
    entity_registry = response.get("entity_registry", {}) if isinstance(response, dict) else {}
    if not isinstance(entity_registry, dict):
        return None
    stage02 = stage_output(run_dir, "stage_02_full_video_asset")
    video_src = ""
    if isinstance(stage02, dict):
        local = stage02.get("local", {})
        if isinstance(local, dict):
            video_src = resolve_video_src(local.get("video_path"), report_html_path)
    usage = payload.get("usage", {}) if isinstance(payload.get("usage"), dict) else {}
    summary = "".join(
        [
            render_summary_card("Model", payload.get("model", "-")),
            render_summary_card("Latency (ms)", f"{float(payload.get('latency_ms', 0.0)):.2f}"),
            render_summary_card("Prompt Tokens", usage.get("prompt_token_count", 0)),
            render_summary_card("Output Tokens", usage.get("candidates_token_count", 0)),
            render_summary_card("Total Tokens", usage.get("total_token_count", 0)),
            render_summary_card("Cost (USD)", f"{float(payload.get('estimated_cost_usd', 0.0)):.6f}"),
        ]
    )

    left = (
        "<div class='card stack'>"
        "<div class='section-title'><h2>Source Video</h2></div>"
        f"{render_video_or_placeholder(video_src, controls=True, muted=True, preload='metadata')}"
        "</div>"
    )
    right = (
        "<div class='stack'>"
        f"{_render_characters(entity_registry.get('characters', []))}"
        f"{_render_key_objects(entity_registry.get('key_objects', []))}"
        f"{_render_ambience(entity_registry.get('ambience_sources', []))}"
        "</div>"
    )
    warnings = render_warning_table(load_stage_warnings(run_dir, "stage_03_agent_a"))
    return f"<div class='summary-grid'>{summary}</div><div class='grid-2'>{left}{right}</div>{warnings}"
