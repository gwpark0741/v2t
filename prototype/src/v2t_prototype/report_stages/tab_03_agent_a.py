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


def _normalize_cut_mappings(response: dict) -> list[dict]:
    cut_mapping = response.get("cut_mapping", {})
    if isinstance(cut_mapping, dict):
        mappings = cut_mapping.get("mappings", [])
        return [item for item in mappings if isinstance(item, dict)]
    if isinstance(cut_mapping, list):
        return [item for item in cut_mapping if isinstance(item, dict)]
    return []


def _render_source_ids(source_ids: list[str], *, label_by_id: dict[str, str], empty_message: str) -> str:
    if not source_ids:
        return f"<span class='muted'>{safe_text(empty_message)}</span>"

    items = []
    for source_id in source_ids:
        label = label_by_id.get(source_id)
        suffix = f" - {label}" if label else ""
        items.append(f"<li><span class='mono'>{safe_text(source_id)}</span>{safe_text(suffix, fallback='')}</li>")
    return "<ul style='margin:0;padding-left:18px'>" + "".join(items) + "</ul>"


def _render_cut_mapping(cuts: list[dict], response: dict, entity_registry: dict) -> str:
    mappings = _normalize_cut_mappings(response)
    mapping_by_cut_id = {
        str(item.get("cut_id")): item for item in mappings if item.get("cut_id") is not None
    }

    sfx_label_by_id: dict[str, str] = {}
    for entity in entity_registry.get("entities", []):
        if not isinstance(entity, dict):
            continue
        children = entity.get("children", [])
        if isinstance(children, list) and children:
            for child in children:
                if isinstance(child, dict) and child.get("id") is not None:
                    sfx_label_by_id[str(child["id"])] = safe_text(child.get("label"))
        elif entity.get("id") is not None:
            sfx_label_by_id[str(entity["id"])] = safe_text(entity.get("label"))

    ambience_label_by_id = {
        str(item.get("id")): safe_text(item.get("label"))
        for item in entity_registry.get("ambience", [])
        if isinstance(item, dict) and item.get("id") is not None
    }

    rows: list[str] = []
    for cut in cuts:
        if not isinstance(cut, dict):
            continue
        cut_id = str(cut.get("id") or "")
        mapping = mapping_by_cut_id.get(cut_id)
        if mapping is None:
            sfx_cell = "<span class='muted'>No mapping found.</span>"
            ambience_cell = "<span class='muted'>No mapping found.</span>"
        else:
            sfx_ids = [str(item) for item in mapping.get("sfx_source_ids", []) if item is not None]
            ambience_ids = [str(item) for item in mapping.get("ambience_source_ids", []) if item is not None]
            sfx_cell = _render_source_ids(
                sfx_ids,
                label_by_id=sfx_label_by_id,
                empty_message="No mapped sfx sources.",
            )
            ambience_cell = _render_source_ids(
                ambience_ids,
                label_by_id=ambience_label_by_id,
                empty_message="No mapped ambience sources.",
            )
        rows.append(
            "<tr>"
            f"<td class='mono'>{safe_text(cut_id)}</td>"
            f"<td>{safe_text(cut.get('start_time'))}</td>"
            f"<td>{safe_text(cut.get('end_time'))}</td>"
            f"<td>{sfx_cell}</td>"
            f"<td>{ambience_cell}</td>"
            "</tr>"
        )

    return (
        "<div class='card'>"
        f"<div class='section-title'><h3>Cut-to-Registry Mapping</h3><span class='badge badge-info'>{len(cuts)}</span></div>"
        "<div class='table-wrap'><table><colgroup>"
        "<col style='width:12%'><col style='width:12%'><col style='width:12%'><col style='width:32%'><col style='width:32%'>"
        "</colgroup><thead><tr><th>Cut ID</th><th>Start</th><th>End</th><th>Mapped SFX Sources</th><th>Mapped Ambience Sources</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div></div>"
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
    cuts: list[dict] = []
    if isinstance(stage02, dict):
        local = stage02.get("local", {})
        if isinstance(local, dict):
            video_src = resolve_video_src(local.get("video_path"), report_html_path)
            raw_cuts = local.get("cuts", [])
            if isinstance(raw_cuts, list):
                cuts = [item for item in raw_cuts if isinstance(item, dict)]
    if not cuts:
        request = payload.get("request", {})
        if isinstance(request, dict):
            raw_cuts = request.get("cuts", [])
            if isinstance(raw_cuts, list):
                cuts = [item for item in raw_cuts if isinstance(item, dict)]
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
        f"{_render_cut_mapping(cuts, response, entity_registry)}"
        "</div>"
    )
    warnings = render_warning_table(load_stage_warnings(run_dir, "stage_03_agent_a"))
    return f"<div class='summary-grid'>{summary}</div><div class='grid-2'>{left}{right}</div>{warnings}"
