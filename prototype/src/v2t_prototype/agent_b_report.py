from __future__ import annotations

import json
from html import escape
from pathlib import Path

from .agent_a_runtime import AgentARuntimeOutput
from .models import AgentBAllCutsResult, UnknownResolution


def _format_seconds(value: float) -> str:
    return f"{value:.3f}s"


def _format_event(event) -> str:
    if hasattr(event, "timestamp"):
        return f'onset @ {_format_seconds(event.timestamp)}'
    return (
        "continuous "
        f"{_format_seconds(event.start_time)} - {_format_seconds(event.end_time)}"
    )


def _format_unknown_resolution(value: UnknownResolution | None) -> str:
    if value is None:
        return "-"
    suggested = value.suggested_entity_id or "-"
    return (
        f"suggestion={escape(value.suggestion)}"
        f"<br/>suggested_entity_id={escape(suggested)}"
        f"<br/>reason={escape(value.reason)}"
    )


def _render_raw_json(raw_text: str) -> str:
    content = raw_text
    try:
        content = json.dumps(json.loads(raw_text), indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        pass
    return f"<pre>{escape(content)}</pre>"


def build_agent_b_report_html(
    result: AgentBAllCutsResult,
    agent_a_output: AgentARuntimeOutput,
    *,
    title: str | None = None,
) -> str:
    report_title = title or "Agent B Report"
    entity_registry = agent_a_output.response.entity_registry
    cut_map = {cut.id: cut for cut in agent_a_output.request.cuts}
    aggregate_usage = result.aggregate_usage

    issue_sections: list[str] = []
    for cut_output in result.cut_outputs:
        cut = cut_map.get(cut_output.cut_id)
        interval = "-"
        if cut is not None:
            interval = f"{_format_seconds(cut.start_time)} - {_format_seconds(cut.end_time)}"

        validation_block = "<p class='muted'>No validation issues.</p>"
        if cut_output.validation_issues:
            validation_items = "".join(
                f"<li>{escape(issue)}</li>" for issue in cut_output.validation_issues
            )
            validation_block = f"<ul>{validation_items}</ul>"

        action_rows = "".join(
            (
                "<tr>"
                f"<td>{escape(action.action_id)}</td>"
                f"<td>{escape(action.primary_source_id)}</td>"
                f"<td>{escape(action.interaction_type)}</td>"
                f"<td>{escape(action.sound_description)}</td>"
                f"<td>{escape(action.observed_visual_description)}</td>"
                f"<td>{escape(_format_event(action.event))}</td>"
                f"<td>{'true' if action.boundary_flag else 'false'}</td>"
                f"<td>{_format_unknown_resolution(action.unknown_resolution)}</td>"
                "</tr>"
            )
            for action in cut_output.actions
        )
        if not action_rows:
            action_rows = (
                "<tr><td colspan='8' class='muted'>No actions returned for this cut.</td></tr>"
            )

        issue_sections.append(
            f"""
    <section class="card">
      <h2>{escape(cut_output.cut_id)}</h2>
      <div class="kv">
        <div class="label">Authoritative Interval</div><div>{escape(interval)}</div>
        <div class="label">Model</div><div>{escape(cut_output.model)}</div>
        <div class="label">Action Count</div><div>{len(cut_output.actions)}</div>
        <div class="label">Latency (ms)</div><div>{cut_output.latency_ms:.2f}</div>
        <div class="label">Prompt Tokens</div><div>{cut_output.usage.prompt_token_count}</div>
        <div class="label">Output Tokens</div><div>{cut_output.usage.candidates_token_count}</div>
        <div class="label">Total Tokens</div><div>{cut_output.usage.total_token_count}</div>
        <div class="label">Estimated Cost (USD)</div><div>{cut_output.estimated_cost_usd:.6f}</div>
      </div>
      <p class="muted">Raw response timestamps below are clip-local. Canonical action timestamps in the table are absolute full-video times.</p>
      <h3>Validation Issues</h3>
      {validation_block}
      <h3>Actions</h3>
      <table>
        <thead>
          <tr>
            <th>Action ID</th><th>Primary Source</th><th>Interaction</th><th>Sound</th>
            <th>Observed Visual</th><th>Event (absolute)</th><th>Boundary</th><th>Unknown Resolution</th>
          </tr>
        </thead>
        <tbody>{action_rows}</tbody>
      </table>
      <h3>Raw Response</h3>
      {_render_raw_json(cut_output.raw_response_text)}
    </section>
"""
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
        for item in result.warnings
    )
    warning_section = ""
    if warning_rows:
        warning_section = f"""
    <section class="card">
      <h2>Warnings</h2>
      <table>
        <thead>
          <tr><th>Severity</th><th>Code</th><th>Message</th><th>Context</th></tr>
        </thead>
        <tbody>{warning_rows}</tbody>
      </table>
    </section>
"""

    skipped_list = ", ".join(result.skipped_cut_ids) if result.skipped_cut_ids else "-"
    failed_list = ", ".join(result.failed_cut_ids) if result.failed_cut_ids else "-"

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
  </style>
</head>
<body>
  <div class="wrap">
    <section class="card">
      <h1>{escape(report_title)}</h1>
      <div class="kv">
        <div class="label">Cut Outputs</div><div>{len(result.cut_outputs)}</div>
        <div class="label">Total Actions</div><div>{result.total_actions}</div>
        <div class="label">Unresolved Count</div><div>{result.unresolved_count}</div>
        <div class="label">Reassigned Count</div><div>{result.reassigned_count}</div>
        <div class="label">Total Model Latency (ms)</div><div>{result.total_model_latency_ms:.2f}</div>
        <div class="label">Prompt Tokens</div><div>{aggregate_usage.prompt_token_count}</div>
        <div class="label">Output Tokens</div><div>{aggregate_usage.candidates_token_count}</div>
        <div class="label">Total Tokens</div><div>{aggregate_usage.total_token_count}</div>
        <div class="label">Estimated Cost (USD)</div><div>{result.estimated_total_cost_usd:.6f}</div>
        <div class="label">Skipped Cut IDs</div><div>{escape(skipped_list)}</div>
        <div class="label">Failed Cut IDs</div><div>{escape(failed_list)}</div>
        <div class="label">Characters</div><div>{len(entity_registry.characters)}</div>
        <div class="label">Key Objects</div><div>{len(entity_registry.key_objects)}</div>
        <div class="label">Ambience Sources</div><div>{len(entity_registry.ambience_sources)}</div>
      </div>
    </section>
    {warning_section}
    {''.join(issue_sections)}
  </div>
</body>
</html>
"""


def write_agent_b_report(
    result: AgentBAllCutsResult,
    agent_a_output: AgentARuntimeOutput,
    output_path: Path,
    *,
    title: str | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        build_agent_b_report_html(
            result,
            agent_a_output,
            title=title,
        ),
        encoding="utf-8",
    )
    return output_path
