from __future__ import annotations

import json
from html import escape
from pathlib import Path

from .models import AgentCResult


def build_agent_c_report_html(
    result: AgentCResult,
    *,
    title: str | None = None,
) -> str:
    report_title = title or "Agent C Report"
    pipeline_result = result.pipeline_result
    tracks = pipeline_result.track_manifest.tracks
    unresolved_unknowns = pipeline_result.unresolved_unknowns
    warnings = pipeline_result.warnings
    total_action_count = sum(len(track.events) for track in tracks) + len(unresolved_unknowns)
    average_flash_latency_ms = (
        result.total_flash_latency_ms / result.flash_call_count
        if result.flash_call_count
        else 0.0
    )

    track_rows = "".join(
        (
            "<tr>"
            f"<td>{escape(track.track_id)}</td>"
            f"<td>{escape(track.track_type)}</td>"
            f"<td>{escape(track.source_entity_id)}</td>"
            f"<td>{escape(track.interaction_type)}</td>"
            f"<td>{escape(track.sound_description)}</td>"
            f"<td>{escape(track.surface_context_summary or '-')}</td>"
            f"<td>{len(track.events)}</td>"
            "</tr>"
        )
        for track in tracks
    )
    if not track_rows:
        track_rows = "<tr><td colspan='7' class='muted'>No tracks synthesized.</td></tr>"

    unresolved_rows = "".join(
        (
            "<tr>"
            f"<td>{escape(item.unknown_id)}</td>"
            f"<td>{escape(item.cut_id)}</td>"
            f"<td>{escape(item.observed_visual_description)}</td>"
            f"<td>{escape(item.interaction_type)}</td>"
            f"<td>{escape(item.sound_description)}</td>"
            "</tr>"
        )
        for item in unresolved_unknowns
    )
    if not unresolved_rows:
        unresolved_rows = (
            "<tr><td colspan='5' class='muted'>No unresolved unknowns.</td></tr>"
        )

    warning_rows = "".join(
        (
            "<tr>"
            f"<td>{escape(item.severity.upper())}</td>"
            f"<td>{escape(item.code)}</td>"
            f"<td>{escape(item.message)}</td>"
            f"<td><pre>{escape(json.dumps(item.context, indent=2, ensure_ascii=False))}</pre></td>"
            "</tr>"
        )
        for item in warnings
    )
    if not warning_rows:
        warning_rows = "<tr><td colspan='4' class='muted'>No warnings recorded.</td></tr>"

    surface_judgment_section = "<p class='muted'>No surface judgments recorded.</p>"
    if result.surface_judgments:
        rows = "".join(
            (
                "<tr>"
                f"<td>{escape(item.action_id_a)}</td>"
                f"<td>{escape(item.action_id_b)}</td>"
                f"<td>{escape(item.interaction_type)}</td>"
                f"<td>{escape(item.surface_context_a or '-')}</td>"
                f"<td>{escape(item.surface_context_b or '-')}</td>"
                f"<td>{escape(item.result)}</td>"
                f"<td><span class='badge badge-{escape(item.source)}'>{escape(item.source)}</span></td>"
                f"<td>{escape(item.reason)}</td>"
                f"<td>{escape(item.model or '-')}</td>"
                "</tr>"
            )
            for item in result.surface_judgments
        )
        surface_judgment_section = f"""
      <table>
        <thead>
          <tr>
            <th>Action A</th><th>Action B</th><th>Interaction</th><th>Surface A</th>
            <th>Surface B</th><th>Result</th><th>Source</th><th>Reason</th><th>Model</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
"""
    per_call_latency_section = "<p class='muted'>No Flash calls recorded.</p>"
    if result.per_call_flash_latency_ms:
        latency_items = "".join(
            f"<li>{index + 1}. {latency_ms:.2f} ms</li>"
            for index, latency_ms in enumerate(result.per_call_flash_latency_ms)
        )
        per_call_latency_section = f"<ol>{latency_items}</ol>"

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
    .wrap {{ max-width: 1200px; margin: 0 auto; }}
    .card {{
      background: #fff;
      border: 1px solid #d8dcea;
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 12px;
    }}
    .kv {{
      display: grid;
      grid-template-columns: 220px 1fr;
      row-gap: 8px;
      column-gap: 10px;
      font-size: 14px;
    }}
    .label {{
      color: #5e6575;
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
    th {{
      color: #5e6575;
      font-weight: 600;
    }}
    h1, h2, h3 {{
      margin: 0 0 12px 0;
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: ui-monospace, "SFMono-Regular", monospace;
      font-size: 12px;
      line-height: 1.45;
    }}
    .muted {{
      color: #5e6575;
    }}
    .badge {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      white-space: nowrap;
    }}
    .badge-flash {{
      background: #dbeafe;
      color: #1d4ed8;
    }}
    .badge-cache_hit {{
      background: #e5e7eb;
      color: #374151;
    }}
    .badge-normalize_match, .badge-null_both, .badge-null_one_side {{
      background: #dcfce7;
      color: #166534;
    }}
    .badge-flash_error {{
      background: #fee2e2;
      color: #b91c1c;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="card">
      <h1>{escape(report_title)}</h1>
      <div class="kv">
        <div class="label">Total Actions</div><div>{total_action_count}</div>
        <div class="label">Merge Group Count</div><div>{result.merge_group_count}</div>
        <div class="label">Track Count</div><div>{len(tracks)}</div>
        <div class="label">Unresolved Unknown Count</div><div>{len(unresolved_unknowns)}</div>
        <div class="label">Warning Count</div><div>{len(warnings)}</div>
        <div class="label">Flash Call Count</div><div>{result.flash_call_count}</div>
        <div class="label">Cache Hit Count</div><div>{result.cache_hit_count}</div>
        <div class="label">Total Flash Latency (ms)</div><div>{result.total_flash_latency_ms:.2f}</div>
        <div class="label">Average Flash Latency (ms)</div><div>{average_flash_latency_ms:.2f}</div>
      </div>
    </section>
    <section class="card">
      <h2>Tracks</h2>
      <table>
        <thead>
          <tr>
            <th>Track ID</th><th>Track Type</th><th>Source Entity</th><th>Interaction</th>
            <th>Sound Description</th><th>Surface Context Summary</th><th>Event Count</th>
          </tr>
        </thead>
        <tbody>{track_rows}</tbody>
      </table>
    </section>
    <section class="card">
      <h2>Unresolved Unknowns</h2>
      <table>
        <thead>
          <tr>
            <th>Unknown ID</th><th>Cut ID</th><th>Observed Visual</th><th>Interaction</th><th>Sound Description</th>
          </tr>
        </thead>
        <tbody>{unresolved_rows}</tbody>
      </table>
    </section>
    <section class="card">
      <h2>Warnings</h2>
      <table>
        <thead>
          <tr><th>Severity</th><th>Code</th><th>Message</th><th>Context</th></tr>
        </thead>
        <tbody>{warning_rows}</tbody>
      </table>
    </section>
    <section class="card">
      <h2>Surface Judgments</h2>
      {surface_judgment_section}
    </section>
    <section class="card">
      <h2>Per-Call Flash Latency</h2>
      {per_call_latency_section}
    </section>
  </div>
</body>
</html>
"""


def write_agent_c_report(
    result: AgentCResult,
    output_path: Path,
    *,
    title: str | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        build_agent_c_report_html(
            result,
            title=title,
        ),
        encoding="utf-8",
    )
    return output_path
