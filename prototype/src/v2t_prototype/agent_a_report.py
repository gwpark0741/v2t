from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Iterable, TypeAlias

from pydantic import BaseModel

from .agent_a import validate_agent_a_response
from .agent_a_runtime import AgentARuntimeOutput
from .models import Ambience, Entity, Entity_Child, FullVideoAssetResult, Unknown


RenderableNode: TypeAlias = Entity | Entity_Child | Ambience


def _format_seconds(value: float) -> str:
    return f"{value:.3f}s"


def _default_video_src(full_video_asset: FullVideoAssetResult, *, video_src: str | None) -> str:
    if video_src:
        return video_src
    path = Path(full_video_asset.local.video_metadata.video_path)
    return path.expanduser().resolve().as_uri()


def _render_entity_tree(label: str, items: Iterable[RenderableNode]) -> str:
    item_list = list(items)
    if not item_list:
        return (
            "<div class='entity-section'>"
            f"<h3>{escape(label)}</h3><p class='muted'>No entries.</p>"
            "</div>"
        )

    def render_node(node: RenderableNode) -> str:
        child_list = "".join(render_node(child) for child in getattr(node, "children", []))
        children_block = f"<div class='entity-children'>{child_list}</div>" if child_list else ""
        return (
            "<div class='entity-card'>"
            f"<p><strong>{escape(node.label)}</strong></p>"
            f"<p>id: {escape(node.id)}</p>"
            f"{children_block}"
            "</div>"
        )

    return (
        "<div class='entity-section'>"
        f"<h3>{escape(label)}</h3>"
        f"{''.join(render_node(item) for item in item_list)}"
        "</div>"
    )


def _render_unknown_list(label: str, items: Iterable[Unknown]) -> str:
    item_list = list(items)
    if not item_list:
        return (
            "<div class='entity-section'>"
            f"<h3>{escape(label)}</h3><p class='muted'>No entries.</p>"
            "</div>"
        )
    rows = []
    for item in item_list:
        rows.append(
            "<div class='entity-card'>"
            f"<p><strong>{escape(item.label)}</strong></p>"
            f"<p>id: {escape(item.id)}<br/>visual_description: {escape(item.visual_description)}</p>"
            "</div>"
        )
    return (
        "<div class='entity-section'>"
        f"<h3>{escape(label)}</h3>"
        f"{''.join(rows)}"
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
    full_video_asset: FullVideoAssetResult,
    runtime_output: AgentARuntimeOutput,
    *,
    title: str | None = None,
    video_src: str | None = None,
) -> str:
    report_title = title or "Agent A Report"
    local = full_video_asset.local
    metadata = local.video_metadata
    request = runtime_output.request
    response = runtime_output.response
    video_src_final = _default_video_src(full_video_asset, video_src=video_src)
    request_json = request.model_dump_json(indent=2)

    timeline_segments: list[str] = []
    cut_rows: list[str] = []
    duration = metadata.duration_seconds
    for cut in local.cuts:
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

    issues = validate_agent_a_response(full_video_asset, response)
    issue_count = len(issues)
    total_entities = (
        len(response.entity_registry.entities)
        + len(response.entity_registry.ambience)
        + len(response.entity_registry.unknowns)
    )
    usage = runtime_output.usage

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
        <div class=\"label\">video_url</div><div>{escape(full_video_asset.video_url)}</div>
        <div class=\"label\">video_mime_type</div><div>{escape(local.video_mime_type)}</div>
      </div>
    </div>
    <div class=\"card\">
      <h2>Preprocessing Output</h2>
      <div class=\"kv\">
        <div class=\"label\">Cut Count</div><div>{len(local.cuts)}</div>
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
        <div class=\"label\">model</div><div>{escape(runtime_output.model or '-')}</div>
        <div class=\"label\">latency_ms</div><div>{runtime_output.latency_ms:.2f}</div>
        <div class=\"label\">prompt_token_count</div><div>{usage.prompt_token_count}</div>
        <div class=\"label\">candidates_token_count</div><div>{usage.candidates_token_count}</div>
        <div class=\"label\">total_token_count</div><div>{usage.total_token_count}</div>
        <div class=\"label\">estimated_cost_usd</div><div>{runtime_output.estimated_cost_usd:.6f}</div>
        <div class=\"label\">entities</div><div>{len(response.entity_registry.entities)}</div>
        <div class=\"label\">ambience</div><div>{len(response.entity_registry.ambience)}</div>
        <div class=\"label\">unknowns</div><div>{len(response.entity_registry.unknowns)}</div>
        <div class=\"label\">total_entities</div><div>{total_entities}</div>
      </div>
    </div>
    <div class=\"card\">
      <h2>Agent A Entity Registry</h2>
      {_render_entity_tree('Entities', response.entity_registry.entities)}
      {_render_entity_tree('Ambience', response.entity_registry.ambience)}
      {_render_unknown_list('Unknowns', response.entity_registry.unknowns)}
    </div>
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
    full_video_asset: FullVideoAssetResult,
    runtime_output: AgentARuntimeOutput,
    output_path: Path,
    *,
    title: str | None = None,
    video_src: str | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html = build_agent_a_report_html(full_video_asset, runtime_output, title=title, video_src=video_src)
    output_path.write_text(html, encoding="utf-8")
    return output_path
