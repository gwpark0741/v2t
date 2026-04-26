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

_SOURCE_COLORS = [
    "#e05a1e",
    "#1a5fb4",
    "#6b3fa0",
    "#1a7a3e",
    "#b45309",
    "#c0392b",
]


def _build_source_display_by_id(run_dir: Path) -> dict[str, str]:
    payload = stage_output(run_dir, "stage_03_agent_a")
    if not isinstance(payload, dict):
        return {}
    response = payload.get("response", {})
    if not isinstance(response, dict):
        return {}
    entity_registry = response.get("entity_registry", {})
    if not isinstance(entity_registry, dict):
        return {}

    source_display_by_id: dict[str, str] = {}

    def register_nodes(items: list[dict]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            parent_id = str(item.get("id", "")).strip()
            if not parent_id:
                continue
            children = item.get("children", [])
            if isinstance(children, list) and children:
                for child in children:
                    if not isinstance(child, dict):
                        continue
                    child_id = str(child.get("id", "")).strip()
                    if not child_id:
                        continue
                    source_display_by_id[child_id] = f"{parent_id} - {child_id}"
            else:
                source_display_by_id[parent_id] = parent_id

    register_nodes(entity_registry.get("entities", []))
    register_nodes(entity_registry.get("ambience", []))

    for item in entity_registry.get("unknowns", []):
        if not isinstance(item, dict):
            continue
        unknown_id = str(item.get("id", "")).strip()
        if unknown_id:
            source_display_by_id[unknown_id] = unknown_id

    return source_display_by_id


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


def _event_color_class(track_type: str) -> str:
    return f"interaction-color-{track_type}"


def _format_event_label(event: dict) -> str:
    event_type = event.get("type")
    if event_type == "onset":
        return "onset"
    if event_type == "continuous":
        return "continuous"
    return safe_text(event_type)


def _render_event_labels(events: list[dict]) -> str:
    labels: list[str] = []
    seen: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        label = _format_event_label(event)
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
    items = [
        f"<div class='mono muted' style='margin-top:4px'>{safe_text(label)}</div>"
        for label in labels
    ]
    return "".join(items)


def _build_track_rows(
    tracks: list[dict],
    duration: float,
    lane_rules: str,
    *,
    source_display_by_id: dict[str, str] | None = None,
) -> str:
    timeline_rows: list[str] = []
    for track in tracks:
        track_type = str(track.get("track_type", "ambience"))
        track_number = int(track.get("track_number", 0) or 0)
        source_id = str(track.get("source_entity_id", "unknown_source"))
        source_display = source_display_by_id.get(source_id, source_id) if source_display_by_id else source_id
        events = track.get("events", [])
        event_html = []
        for event in events:
            if not isinstance(event, dict) or not _is_valid_event(event, duration):
                continue
            if event.get("type") == "onset":
                left = _to_percent(float(event.get("timestamp", 0.0)), duration)
                event_html.append(
                    f"<div class='timeline-event-onset {_event_color_class(track_type)}' "
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
                    f"<div class='timeline-event-continuous {_event_color_class(track_type)}' "
                    f"style='left:{_to_percent(start_time, duration):.4f}%; width:max({width:.4f}%, 3px)' "
                    f"title=\"{safe_text(track.get('sound_description'))}\"></div>"
                )
        timeline_rows.append(
            "<div class='timeline-row'>"
            "<div class='timeline-label'>"
            f"<div class='mono break-word'>Track {track_number:02d}</div>"
            f"<div class='mono break-word' style='margin-top:4px'>{safe_text(track.get('track_id'))}</div>"
            f"<div class='mono muted break-word' style='margin-top:4px'>{safe_text(source_display)}</div>"
            f"<div class='break-word' style='margin-top:6px; color: var(--text-secondary)'>{safe_text(track.get('sound_description'))}</div>"
            f"{_render_event_labels(events if isinstance(events, list) else [])}"
            f"<div style='margin-top:4px'>{render_badge(track_type)}</div>"
            "</div>"
            f"<div class='timeline-lane'><div class='timeline-lane-inner'>{lane_rules}{''.join(event_html)}</div></div>"
            "</div>"
        )
    if not timeline_rows:
        return '<div class="empty-state">No tracks available</div>'
    return "".join(timeline_rows)


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
            render_summary_card("LLM Calls", payload.get("llm_call_count", 0)),
            render_summary_card(
                "LLM Tokens",
                payload.get("llm_usage", {}).get("total_token_count", 0),
            ),
            render_summary_card(
                "LLM Cost (USD)",
                f"{float(payload.get('estimated_llm_cost_usd', 0.0)):.6f}",
            ),
            render_summary_card(
                "LLM Latency (ms)",
                f"{float(payload.get('total_llm_latency_ms', 0.0)):.2f}",
            ),
        ]
    )

    video_id = "pipeline-report-source-video"
    lane_rules = _render_lane_rules(duration)
    source_groups: dict[str, list[dict]] = {}
    for track in tracks:
        if not isinstance(track, dict):
            continue
        source_groups.setdefault(str(track.get("source_entity_id", "unknown_source")), []).append(track)
    source_ids = list(source_groups.keys())
    source_color_by_id = {
        source_id: _SOURCE_COLORS[index % len(_SOURCE_COLORS)]
        for index, source_id in enumerate(source_ids)
    }
    source_display_by_id = _build_source_display_by_id(run_dir)
    all_tracks = [track for track in tracks if isinstance(track, dict)]

    source_sections: list[str] = []
    for index, source_id in enumerate(source_ids, start=1):
        source_color = source_color_by_id[source_id]
        source_tracks = source_groups[source_id]
        accordion_id = f"stage06-source-{index}"
        source_heading = source_display_by_id.get(source_id, source_id)
        source_sections.append(
            "<div class='accordion-item' style='margin-top:16px'>"
            f"<button class='accordion-trigger' type='button' data-accordion-target='{accordion_id}' "
            f"style='border-left: 6px solid {source_color}; background: linear-gradient(to right, {source_color}14, var(--bg-surface) 18%)'>"
            "<div class='accordion-meta'>"
            f"<span class='mono truncate'>{safe_text(source_heading)}</span>"
            f"<span class='badge badge-info'>{len(source_tracks)} tracks</span>"
            "</div>"
            f"<span class='muted'>source group</span>"
            "</button>"
            f"<div class='accordion-content open' id='{accordion_id}'>"
            f"<div class='timeline-wrap' data-video-id='{video_id}' style='margin-top:12px; border-left: 6px solid {source_color}'>"
            "<div class='timeline-overlay'><div class='timeline-cursor'></div></div>"
            "<div class='timeline-ruler-row'>"
            "<div class='timeline-ruler-label'></div>"
            f"<div class='timeline-ruler-lane'><div class='timeline-ruler-inner'>{_render_rule_lines(duration)}</div></div>"
            "</div>"
            f"{_build_track_rows(source_tracks, duration, lane_rules, source_display_by_id=source_display_by_id)}"
            "</div>"
            "</div>"
            "</div>"
        )

    all_tracks_section = (
        "<div class='timeline-wrap' data-video-id='pipeline-report-source-video'>"
        "<div class='timeline-overlay'><div class='timeline-cursor'></div></div>"
        "<div class='timeline-ruler-row'>"
        "<div class='timeline-ruler-label'></div>"
        f"<div class='timeline-ruler-lane'><div class='timeline-ruler-inner'>{_render_rule_lines(duration)}</div></div>"
        "</div>"
        f"{_build_track_rows(all_tracks, duration, lane_rules, source_display_by_id=source_display_by_id)}"
        "</div>"
    )

    warnings = render_warning_table(load_stage_warnings(run_dir, "stage_06_agent_c"))
    judgments = payload.get("track_group_judgments", [])
    judgment_title = "Track Group Judgments"
    judgment_section = ""
    if isinstance(judgments, list):
        if judgments:
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
        "<div class='tracks-layout'>"
        "<div class='tracks-sidebar'>"
        "<div class='card stack'>"
        "<div class='section-title'><h2>Source Video</h2></div>"
        f"{render_video_or_placeholder(video_src, controls=True, muted=True, element_id=video_id)}"
        "</div>"
        "</div>"
        "<div class='tracks-scroll-panel'>"
        "<div class='card' data-subtab-group='stage06-track-views'>"
        "<div class='section-title'><h2>Track Views</h2></div>"
        "<div class='subtabs'>"
        "<button class='subtab-button' type='button' data-subtab-target='stage06-all-tracks'>All Tracks</button>"
        "<button class='subtab-button' type='button' data-subtab-target='stage06-by-source'>By Source</button>"
        "</div>"
        f"<div class='subtab-panel' data-subtab-panel='stage06-all-tracks'>{all_tracks_section}</div>"
        f"<div class='subtab-panel' data-subtab-panel='stage06-by-source'>{''.join(source_sections) if source_sections else '<div class=\"empty-state\">No tracks available</div>'}</div>"
        "</div>"
        f"{judgment_section}"
        f"{warnings}"
        "</div>"
        "</div>"
    )
