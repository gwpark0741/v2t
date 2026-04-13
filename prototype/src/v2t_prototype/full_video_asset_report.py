from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Sequence

from .models import FullVideoAssetResult, WarningItem


def build_full_video_asset_report_html(
    result: FullVideoAssetResult,
    *,
    title: str | None = None,
    warnings: Sequence[WarningItem] | None = None,
) -> str:
    report_title = title or "Full Video Asset Report"
    metadata = result.local.video_metadata
    report_warnings = list(warnings or [])
    warning_rows = "".join(
        (
            "<tr>"
            f"<td>{escape(item.severity.upper())}</td>"
            f"<td>{escape(item.code)}</td>"
            f"<td>{escape(item.message)}</td>"
            f"<td><pre>{escape(str(item.context))}</pre></td>"
            "</tr>"
        )
        for item in report_warnings
    )
    warning_section = ""
    if report_warnings:
        warning_section = f"""
    <div class="card">
      <h2>Warnings</h2>
      <table>
        <thead>
          <tr><th>Severity</th><th>Code</th><th>Message</th><th>Context</th></tr>
        </thead>
        <tbody>
          {warning_rows}
        </tbody>
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
    :root {{
      --bg: #f6f7fb;
      --card: #ffffff;
      --text: #1c2333;
      --muted: #5e6575;
      --line: #d8dcea;
      --ok: #216b44;
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .wrap {{
      max-width: 960px;
      margin: 24px auto;
      padding: 0 16px 24px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 12px;
    }}
    .pill {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      background: #e7f4ec;
      color: var(--ok);
      font-size: 13px;
      font-weight: 600;
    }}
    .kv {{
      display: grid;
      grid-template-columns: 220px 1fr;
      row-gap: 8px;
      column-gap: 10px;
      font-size: 14px;
    }}
    .label {{
      color: var(--muted);
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: ui-monospace, "SFMono-Regular", monospace;
      font-size: 12px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      text-align: left;
      padding: 8px;
      border-bottom: 1px solid var(--line);
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>{escape(report_title)}</h1>
      <div class="pill">ACTIVE</div>
      <div class="kv" style="margin-top: 12px;">
        <div class="label">Video Path</div><div>{escape(result.local.video_path)}</div>
        <div class="label">Video URL</div><div>{escape(result.video_url)}</div>
        <div class="label">Gemini File Name</div><div>{escape(result.gemini_file_name)}</div>
        <div class="label">Upload Timestamp (UTC)</div><div>{escape(result.upload_timestamp_utc)}</div>
      </div>
    </div>
    <div class="card">
      <h2>Stage 01 Summary</h2>
      <div class="kv">
        <div class="label">FPS</div><div>{metadata.fps}</div>
        <div class="label">Frame Count</div><div>{metadata.frame_count}</div>
        <div class="label">Duration</div><div>{metadata.duration_seconds:.3f}s</div>
        <div class="label">Resolution</div><div>{metadata.width} x {metadata.height}</div>
        <div class="label">Cut Count</div><div>{len(result.local.cuts)}</div>
        <div class="label">MIME Type</div><div>{escape(result.local.video_mime_type)}</div>
      </div>
    </div>
    {warning_section}
  </div>
</body>
</html>
"""


def write_full_video_asset_report(
    result: FullVideoAssetResult,
    output_path: Path,
    *,
    title: str | None = None,
    warnings: Sequence[WarningItem] | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html = build_full_video_asset_report_html(result, title=title, warnings=warnings)
    output_path.write_text(html, encoding="utf-8")
    return output_path
