from __future__ import annotations

from pathlib import Path

from .common import (
    render_badge,
    render_warning_table,
    load_stage_warnings,
    safe_text,
    stage_output,
)


def render_tab(run_dir: Path, report_html_path: Path) -> str | None:
    del report_html_path
    payload = stage_output(run_dir, "stage_02_full_video_asset")
    if not isinstance(payload, dict):
        return None

    body = (
        "<div class='card stack'>"
        "<div class='section-title'><h2>Upload Summary</h2>"
        f"{render_badge('success', kind='severity')}</div>"
        "<div class='kv-grid'>"
        f"<div class='kv-label'>Gemini File Name</div><div class='mono truncate'>{safe_text(payload.get('gemini_file_name'))}</div>"
        f"<div class='kv-label'>Video URL</div><div class='mono truncate'>{safe_text(payload.get('video_url'))}</div>"
        f"<div class='kv-label'>Uploaded At</div><div class='mono'>{safe_text(payload.get('upload_timestamp_utc'))}</div>"
        "</div>"
        "</div>"
    )
    warnings = render_warning_table(load_stage_warnings(run_dir, "stage_02_full_video_asset"))
    return body + warnings
