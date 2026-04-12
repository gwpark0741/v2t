from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel

from .agent_a import validate_agent_a_response
from .agent_a_runtime import AgentARuntimeOutput
from .models import PreprocessingResult


def _format_seconds(value: float) -> str:
    return f"{value:.3f}s"


def _default_video_src(preprocessing: PreprocessingResult, *, video_src: str | None) -> str:
    if video_src:
        return video_src
    path = Path(preprocessing.video_metadata.video_path)
    return path.expanduser().resolve().as_uri()


def _render_entity_list(label: str, items: Iterable[BaseModel]) -> str:
    if not items:
        return (
            "<div class='entity-section'>"
            f"<h3>{escape(label)}</h3><p class='muted'>No entries.</p>"
            "</div>"
        )

    rows = []
    for item in items:
        fields = "<br/>".join(
            f"{escape(name)}: {escape(str(getattr(item, name)))}"
            for name in type(item).model_fields
        )
        rows.append(f"<div class='entity-card'><p>{fields}</p></div>")
    return (
        "<div class='entity-section'>"
        f"<h3>{escape(label)}</h3>"
        f"{''.join(rows)}"
        "</div>"
    )


def _render_cut_enrichments(enrichments: Iterable, force_empty: bool = False) -> str:
    enrichment_list = list(enrichments)
    if not enrichment_list and not force_empty:
        return ""

    rows = []
    for enrichment in enrichment_list:
        rows.append(
            "<tr>"
            f"<td>{escape(enrichment.cut_id)}</td>"
            f"<td>{escape(enrichment.camera_angle)}</td>"
            f"<td>{escape(enrichment.transition_type)}</td>"
            f"<td>{escape(enrichment.camera_notes)}</td>"
            "</tr>"
        )
    body = "".join(rows)
    return (
        "<div class='card'>"
        "<h2>Cut Enrichments</h2>"
        "<table>"
        "<thead><tr><th>Cut</th><th>Camera</th><th>Transition</th><th>Notes</th></tr></thead>"
        f"<tbody>{body}</tbody>"
        "</table>"
        "</div>"
    )


def _render_validation_summary(issues: list[str]) -> str:
    if not issues:
        return "<p>PASS - No validation issues detected.</p>"
    items = "".join(f"<li>{escape(issue)}</li>" for issue in issues)
    return "<p>FAIL - Validation issues detected.</p><ul>" + items + "</ul>"


def _render_json_block(raw_text: str) -> str:
    content = raw_text
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        pass
    else:
        content = json.dumps(parsed, indent=2, ensure_ascii=False)
    return f"<pre>{escape(content)}</pre>"


def build_agent_a_report_html(
    preprocessing: PreprocessingResult,
    runtime_output: AgentARuntimeOutput,
    *,
    title: str | None = None,
    video_src: str | None = None,
) -> str:
    report_title = title or "Agent A Report"
    metadata = preprocessing.video_metadata
    request = runtime_output.request
    response = runtime_output.response
    video_src_final = _default_video_src(preprocessing, video_src=video_src)
    request_json = request.model_dump_json(indent=2)

    timeline_segments: list[str] = []
    cut_rows: list[str] = []
    duration = metadata.duration_seconds
    for cut in preprocessing.cuts:
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

    issues = validate_agent_a_response(preprocessing, response)
    issue_count = len(issues)
    total_entities = (
        len(response.entity_registry.characters)
        + len(response.entity_registry.key_objects)
        + len(response.entity_registry.ambience_sources)
    )

    html = f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{escape(report_title)}</title>
  <style>
    :root {{
      --bg: #f6f7fb;
      --card: #ffffff;
      --text: #1c2333;
      --muted: #5e6575;
      --line: #d8dcea;
      --accent: #2f7a4a;
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
    h1, h2, h3 {{
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
    .video-player {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #000;
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
      background: var(--accent);
      border-right: 1px solid #ffffff66;
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
    .entity-section {{
      margin-bottom: 12px;
    }}
    .entity-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px;
      margin-bottom: 8px;
      background: #fcfcfd;
      font-size: 13px;
    }}
    .muted {{
      color: var(--muted);
      font-size: 13px;
    }}
    pre {{
      margin: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f2f4fa;
      padding: 10px;
      font-size: 12px;
      line-height: 1.45;
      overflow-x: auto;
      white-space: pre-wrap;
    }}
    .status-pill {{
      display: inline-block;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 600;
      background: #e8f5ec;
      color: #14532d;
    }}
    .status-pill.fail {{
      background: #fde8e8;
      color: #9b1c1c;
    }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"card\">
      <h1>{escape(report_title)}</h1>
      <video class=\"video-player\" controls src=\"{escape(video_src_final)}\">
        Your browser does not support the video tag.
      </video>
    </div>
    <div class=\"card\">
      <h2>Input Video Summary</h2>
      <div class=\"kv\">
        <div class=\"label\">Input Path</div><div>{escape(metadata.video_path)}</div>
        <div class=\"label\">Video Player Source</div><div>{escape(video_src_final)}</div>
        <div class=\"label\">Duration</div><div>{_format_seconds(metadata.duration_seconds)}</div>
        <div class=\"label\">FPS</div><div>{metadata.fps}</div>
        <div class=\"label\">Resolution</div><div>{metadata.width} x {metadata.height}</div>
      </div>
    </div>
    <div class=\"card\">
      <h2>Upload Result</h2>
      <div class=\"kv\">
        <div class=\"label\">video_url</div><div>{escape(preprocessing.video_url)}</div>
        <div class=\"label\">video_mime_type</div><div>{escape(preprocessing.video_mime_type)}</div>
      </div>
    </div>
    <div class=\"card\">
      <h2>Preprocessing Output</h2>
      <div class=\"kv\">
        <div class=\"label\">Cut Count</div><div>{len(preprocessing.cuts)}</div>
        <div class=\"label\">Frame Count</div><div>{metadata.frame_count}</div>
      </div>
    </div>
    <div class=\"card\">
      <h2>Preprocessing Timeline</h2>
      <div class=\"timeline-track\">{''.join(timeline_segments)}</div>
    </div>
    <div class=\"card\">
      <h2>Preprocessing Cuts</h2>
      <table>
        <thead>
          <tr><th>ID</th><th>Start</th><th>End</th><th>Duration</th></tr>
        </thead>
        <tbody>
          {''.join(cut_rows)}
        </tbody>
      </table>
    </div>
    <div class=\"card\">
      <h2>Agent A Request Summary</h2>
      <div class=\"kv\">
        <div class=\"label\">video_url</div><div>{escape(request.video_url)}</div>
        <div class=\"label\">video_mime_type</div><div>{escape(request.video_mime_type)}</div>
        <div class=\"label\">cuts in request</div><div>{len(request.cuts)}</div>
      </div>
    </div>
    <div class=\"card\">
      <h2>Agent A Request JSON</h2>
      {_render_json_block(request_json)}
    </div>
    <div class=\"card\">
      <h2>Agent A Response Summary</h2>
      <div class=\"kv\">
        <div class=\"label\">characters</div><div>{len(response.entity_registry.characters)}</div>
        <div class=\"label\">key_objects</div><div>{len(response.entity_registry.key_objects)}</div>
        <div class=\"label\">ambience_sources</div><div>{len(response.entity_registry.ambience_sources)}</div>
        <div class=\"label\">cut_enrichments</div><div>{len(response.cut_enrichments)}</div>
        <div class=\"label\">total_entities</div><div>{total_entities}</div>
      </div>
    </div>
    <div class=\"card\">
      <h2>Agent A Entity Registry</h2>
      {_render_entity_list('Characters', response.entity_registry.characters)}
      {_render_entity_list('Key Objects', response.entity_registry.key_objects)}
      {_render_entity_list('Ambience Sources', response.entity_registry.ambience_sources)}
    </div>
    {_render_cut_enrichments(response.cut_enrichments, force_empty=True)}
    <div class=\"card\">
      <h2>Raw Gemini JSON Text</h2>
      {_render_json_block(runtime_output.raw_response_text)}
    </div>
    <div class=\"card\">
      <h2>Validation Summary</h2>
      <p>
        <span class=\"status-pill{' fail' if issue_count else ''}\">
          {'PASS' if issue_count == 0 else 'FAIL'}
        </span>
      </p>
      {_render_validation_summary(issues)}
    </div>
  </div>
</body>
</html>"""
    return html


def write_agent_a_report(
    preprocessing: PreprocessingResult,
    runtime_output: AgentARuntimeOutput,
    output_path: Path,
    *,
    title: str | None = None,
    video_src: str | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html = build_agent_a_report_html(preprocessing, runtime_output, title=title, video_src=video_src)
    output_path.write_text(html, encoding="utf-8")
    return output_path
