from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Sequence

from .models import LocalPreprocessingResult, PreprocessingResult, WarningItem


def _format_seconds(value: float) -> str:
    return f"{value:.3f}s"


def build_preprocessing_report_html(
    result: PreprocessingResult | LocalPreprocessingResult,
    *,
    title: str | None = None,
    warnings: Sequence[WarningItem] | None = None,
) -> str:
    """전처리 결과를 단일 정적 HTML 문자열로 렌더링합니다."""
    report_title = title or "Preprocessing Report"
    metadata = result.video_metadata
    cut_count = len(result.cuts)
    duration = metadata.duration_seconds
    report_warnings = list(warnings or [])

    timeline_segments: list[str] = []
    cut_rows: list[str] = []
    for cut in result.cuts:
        cut_duration = cut.end_time - cut.start_time
        start_percent = 0.0 if duration <= 0 else (cut.start_time / duration) * 100.0
        width_percent = 0.0 if duration <= 0 else (cut_duration / duration) * 100.0
        timeline_segments.append(
            (
                "<div class='segment' "
                f"style='left:{start_percent:.4f}%;width:{width_percent:.4f}%;' "
                f"title='{escape(cut.id)}: {_format_seconds(cut.start_time)} - {_format_seconds(cut.end_time)}'></div>"
            )
        )
        cut_rows.append(
            "<tr>"
            f"<td>{escape(cut.id)}</td>"
            f"<td>{_format_seconds(cut.start_time)}</td>"
            f"<td>{_format_seconds(cut.end_time)}</td>"
            f"<td>{_format_seconds(cut_duration)}</td>"
            "</tr>"
        )

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

    # JavaScript 없이도 컷 비율을 직관적으로 확인할 수 있도록 절대 배치 막대를 사용합니다.
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
      --segment: #2f7a4a;
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
    h1, h2 {{
      margin: 0 0 12px 0;
    }}
    .kv {{
      display: grid;
      grid-template-columns: 200px 1fr;
      row-gap: 8px;
      column-gap: 10px;
      font-size: 14px;
    }}
    .kv .label {{
      color: var(--muted);
    }}
    .timeline-track {{
      position: relative;
      height: 26px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #eef1f7;
      overflow: hidden;
    }}
    .segment {{
      position: absolute;
      top: 0;
      bottom: 0;
      background: var(--segment);
      border-right: 1px solid #ffffff66;
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
      <div class="kv">
        <div class="label">Video Path</div><div>{escape(metadata.video_path)}</div>
        <div class="label">FPS</div><div>{metadata.fps}</div>
        <div class="label">Frame Count</div><div>{metadata.frame_count}</div>
        <div class="label">Duration</div><div>{_format_seconds(metadata.duration_seconds)}</div>
        <div class="label">Resolution</div><div>{metadata.width} x {metadata.height}</div>
        <div class="label">MIME Type</div><div>{escape(result.video_mime_type)}</div>
        <div class="label">Cut Count</div><div>{cut_count}</div>
      </div>
    </div>
    {warning_section}
    <div class="card">
      <h2>Timeline</h2>
      <div class="timeline-track">{''.join(timeline_segments)}</div>
    </div>
    <div class="card">
      <h2>Cuts</h2>
      <table>
        <thead>
          <tr><th>ID</th><th>Start</th><th>End</th><th>Duration</th></tr>
        </thead>
        <tbody>
          {''.join(cut_rows)}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""


def write_preprocessing_report(
    result: PreprocessingResult | LocalPreprocessingResult,
    output_path: Path,
    *,
    title: str | None = None,
    warnings: Sequence[WarningItem] | None = None,
) -> Path:
    """전처리 HTML 리포트를 파일로 저장하고 저장 경로를 반환합니다."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html = build_preprocessing_report_html(result, title=title, warnings=warnings)
    output_path.write_text(html, encoding="utf-8")
    return output_path
