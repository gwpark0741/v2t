from __future__ import annotations

from pathlib import Path

from .common import load_stage_warnings, render_summary_card, render_video_or_placeholder, render_warning_table, resolve_video_src, safe_text, stage_output


def _render_entity_tree(label: str, items: list[dict]) -> str:
    def render_node(item: dict) -> str:
        children = item.get("children", [])
        child_cards = "".join(render_node(child) for child in children if isinstance(child, dict))
        children_block = f"<div style='margin-top:8px;padding-left:16px'>{child_cards}</div>" if child_cards else ""
        return (
            "<div class='entity-card'>"
            f"<div><strong>{safe_text(item.get('label'))}</strong></div>"
            f"<div class='mono truncate'>{safe_text(item.get('id'))}</div>"
            f"{children_block}"
            "</div>"
        )

    return (
        "<div class='card'>"
        f"<div class='section-title'><h3>{safe_text(label)}</h3><span class='badge badge-info'>{len(items)}</span></div>"
        f"{''.join(render_node(item) for item in items if isinstance(item, dict))}"
        "</div>"
    )


def _render_unknowns(unknowns: list[dict]) -> str:
    rows = "".join(
        "<tr>"
        f"<td class='mono truncate'>{safe_text(item.get('id'))}</td>"
        f"<td>{safe_text(item.get('label'))}</td>"
        f"<td><div class='break-word'>{safe_text(item.get('visual_description'))}</div></td>"
        "</tr>"
        for item in unknowns
    )
    return (
        "<div class='card'>"
        f"<div class='section-title'><h3>Unknowns</h3><span class='badge badge-info'>{len(unknowns)}</span></div>"
        "<div class='table-wrap'><table><colgroup>"
        "<col style='width:18%'><col style='width:18%'><col style='width:64%'>"
        "</colgroup><thead><tr><th>ID</th><th>Label</th><th>Visual Description</th></tr></thead>"
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
        f"{_render_entity_tree('Entities', entity_registry.get('entities', []))}"
        f"{_render_entity_tree('Ambience', entity_registry.get('ambience', []))}"
        f"{_render_unknowns(entity_registry.get('unknowns', []))}"
        "</div>"
    )
    warnings = render_warning_table(load_stage_warnings(run_dir, "stage_03_agent_a"))
    return f"<div class='summary-grid'>{summary}</div><div class='grid-2'>{left}{right}</div>{warnings}"
