from __future__ import annotations

from pathlib import Path

from .common import (
    load_stage_warnings,
    render_summary_card,
    render_video_or_placeholder,
    render_warning_table,
    resolve_video_src,
    safe_text,
    stage_output,
)


def render_tab(run_dir: Path, report_html_path: Path) -> str | None:
    payload = stage_output(run_dir, "stage_01_local_preprocessing")
    if not isinstance(payload, dict):
        return None

    metadata = payload.get("video_metadata", {})
    cuts = payload.get("cuts", [])
    if not isinstance(metadata, dict) or not isinstance(cuts, list):
        return None

    summary = "".join(
        [
            render_summary_card("Video File", Path(str(payload.get("video_path", ""))).name or "—"),
            render_summary_card("Duration", f"{float(metadata.get('duration_seconds', 0.0)):.3f}s"),
            render_summary_card("Cut Count", len(cuts)),
            render_summary_card("Resolution", f"{metadata.get('width', '—')}x{metadata.get('height', '—')}"),
            render_summary_card("FPS", metadata.get("fps", "—")),
        ]
    )

    cards = []
    for cut in cuts:
        if not isinstance(cut, dict):
            continue
        cut_id = str(cut.get("id", ""))
        start_time = float(cut.get("start_time", 0.0))
        end_time = float(cut.get("end_time", 0.0))
        duration = max(0.0, end_time - start_time)
        clip_path = run_dir / "stage_04_segment_prep" / "clips" / f"{cut_id}.mp4"
        video_html = render_video_or_placeholder(
            resolve_video_src(str(clip_path), report_html_path) if clip_path.exists() else "",
            controls=True,
            muted=True,
            preload="metadata",
        )
        cards.append(
            "<article class='card stack'>"
            "<div class='section-title'>"
            f"<h3 class='mono truncate'>{safe_text(cut_id)}</h3>"
            "<span class='badge badge-info'>cut clip</span>"
            "</div>"
            f"{video_html}"
            "<div class='kv-grid'>"
            f"<div class='kv-label'>Interval</div><div class='mono'>{start_time:.3f}s ~ {end_time:.3f}s</div>"
            f"<div class='kv-label'>Duration</div><div class='mono'>{duration:.3f}s</div>"
            "</div>"
            "</article>"
        )

    warnings = render_warning_table(load_stage_warnings(run_dir, "stage_01_local_preprocessing"))
    return (
        f"<div class='summary-grid'>{summary}</div>"
        f"<div class='grid-cards'>{''.join(cards) if cards else '<div class=\"empty-state\">No cuts found</div>'}</div>"
        f"{warnings}"
    )
