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
    payload = stage_output(run_dir, "stage_04_segment_prep")
    if not isinstance(payload, dict):
        return None
    clips = payload.get("clips", [])
    if not isinstance(clips, list):
        return None
    stage01 = stage_output(run_dir, "stage_01_local_preprocessing")
    cut_by_id = {}
    if isinstance(stage01, dict):
        for cut in stage01.get("cuts", []):
            if isinstance(cut, dict):
                cut_by_id[str(cut.get("id", ""))] = cut

    uploaded_count = sum(1 for clip in clips if isinstance(clip, dict) and clip.get("clip_video_url"))
    summary = "".join(
        [
            render_summary_card("Total Clips", len(clips)),
            render_summary_card("Uploaded Clips", uploaded_count),
        ]
    )

    cards = []
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        cut_id = str(clip.get("cut_id", ""))
        cut = cut_by_id.get(cut_id, {})
        start_time = cut.get("start_time")
        end_time = cut.get("end_time")
        if start_time is not None and end_time is not None:
            interval_html = f"{float(start_time):.3f}s ~ {float(end_time):.3f}s"
        else:
            interval_html = "—"
        local_clip_path = clip.get("local_clip_path", "")
        cards.append(
            "<article class='card stack'>"
            "<div class='section-title'>"
            f"<h3 class='mono truncate'>{safe_text(cut_id)}</h3>"
            "<span class='badge badge-info'>no padding</span>"
            "</div>"
            f"{render_video_or_placeholder(resolve_video_src(local_clip_path, report_html_path), controls=True, muted=True)}"
            "<div class='kv-grid'>"
            f"<div class='kv-label'>Interval</div><div class='mono'>{interval_html}</div>"
            f"<div class='kv-label'>Label</div><div class='mono'>authoritative clip</div>"
            "</div>"
            "</article>"
        )

    warnings = render_warning_table(load_stage_warnings(run_dir, "stage_04_segment_prep"))
    return f"<div class='summary-grid'>{summary}</div><div class='grid-cards'>{''.join(cards)}</div>{warnings}"
