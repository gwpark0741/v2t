from __future__ import annotations

from html import escape
from pathlib import Path

from .models import SegmentPrepResult


def build_segment_prep_report_html(
    result: SegmentPrepResult,
    *,
    source_video_path: str,
    title: str | None = None,
) -> str:
    report_title = title or "Segment Prep Report"
    clip_rows = "".join(
        (
            "<tr>"
            f"<td>{escape(clip.cut_id)}</td>"
            f"<td>{escape(clip.local_clip_path)}</td>"
            f"<td>{escape(clip.clip_video_url)}</td>"
            f"<td>{clip.padded_start_time:.3f}</td>"
            f"<td>{clip.padded_end_time:.3f}</td>"
            "</tr>"
        )
        for clip in result.clips
    )
    skipped_rows = "".join(
        (
            "<tr>"
            f"<td>{escape(skipped.cut_id)}</td>"
            f"<td>{escape(skipped.reason)}</td>"
            f"<td>{escape(skipped.error_detail)}</td>"
            "</tr>"
        )
        for skipped in result.skipped_cuts
    )
    warning_rows = "".join(
        (
            "<tr>"
            f"<td>{escape(item.severity.upper())}</td>"
            f"<td>{escape(item.code)}</td>"
            f"<td>{escape(item.message)}</td>"
            "</tr>"
        )
        for item in result.warnings
    )
    skipped_section = ""
    if skipped_rows:
        skipped_section = f"""
    <div class="card">
      <h2>Skipped Cuts</h2>
      <table>
        <thead>
          <tr><th>Cut ID</th><th>Reason</th><th>Error Detail</th></tr>
        </thead>
        <tbody>{skipped_rows}</tbody>
      </table>
    </div>
"""
    warning_section = ""
    if warning_rows:
        warning_section = f"""
    <div class="card">
      <h2>Warnings</h2>
      <table>
        <thead>
          <tr><th>Severity</th><th>Code</th><th>Message</th></tr>
        </thead>
        <tbody>{warning_rows}</tbody>
      </table>
    </div>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(report_title)}</title>
  <style>
    body {{
      margin: 0;
      padding: 24px 16px;
      background: #f6f7fb;
      color: #1c2333;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .wrap {{ max-width: 1040px; margin: 0 auto; }}
    .card {{
      background: #fff;
      border: 1px solid #d8dcea;
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 12px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      text-align: left;
      padding: 8px;
      border-bottom: 1px solid #d8dcea;
      vertical-align: top;
    }}
    th {{ color: #5e6575; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>{escape(report_title)}</h1>
      <p><strong>Source Video:</strong> {escape(source_video_path)}</p>
      <p><strong>Successful Clips:</strong> {len(result.clips)}</p>
      <p><strong>Skipped Cuts:</strong> {len(result.skipped_cuts)}</p>
      <p><strong>Warnings:</strong> {len(result.warnings)}</p>
    </div>
    <div class="card">
      <h2>Successful Clips</h2>
      <table>
        <thead>
          <tr><th>Cut ID</th><th>Local Clip Path</th><th>Clip Video URL</th><th>Padded Start</th><th>Padded End</th></tr>
        </thead>
        <tbody>{clip_rows}</tbody>
      </table>
    </div>
    {skipped_section}
    {warning_section}
  </div>
</body>
</html>
"""


def write_segment_prep_report(
    result: SegmentPrepResult,
    output_path: Path,
    *,
    source_video_path: str,
    title: str | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        build_segment_prep_report_html(
            result,
            source_video_path=source_video_path,
            title=title,
        ),
        encoding="utf-8",
    )
    return output_path
