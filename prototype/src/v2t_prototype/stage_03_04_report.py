from __future__ import annotations

from html import escape
from pathlib import Path

from .agent_a import validate_agent_a_response
from .agent_a_runtime import AgentARuntimeOutput
from .models import PreprocessingResult, SegmentPrepResult


def _format_seconds(value: float) -> str:
    return f"{value:.3f}s"


def _as_file_uri(path: str) -> str:
    return Path(path).expanduser().resolve().as_uri()


def build_stage_03_04_report_html(
    preprocessing: PreprocessingResult,
    agent_a_output: AgentARuntimeOutput,
    segment_prep: SegmentPrepResult,
    *,
    title: str | None = None,
) -> str:
    report_title = title or "Stage 01/03/04 Pair Review Report"
    metadata = preprocessing.video_metadata
    cut_by_id = {cut.id: cut for cut in preprocessing.cuts}
    enrichment_by_cut_id = {
        enrichment.cut_id: enrichment
        for enrichment in agent_a_output.response.cut_enrichments
    }
    clip_by_cut_id = {clip.cut_id: clip for clip in segment_prep.clips}
    skipped_by_cut_id = {skipped.cut_id: skipped for skipped in segment_prep.skipped_cuts}
    validation_issues = validate_agent_a_response(preprocessing, agent_a_output.response)

    pair_sections: list[str] = []
    for cut in preprocessing.cuts:
        enrichment = enrichment_by_cut_id.get(cut.id)
        clip = clip_by_cut_id.get(cut.id)
        skipped = skipped_by_cut_id.get(cut.id)

        clip_block = "<p class='muted'>No segment clip available.</p>"
        if clip is not None:
            clip_block = (
                f"<video controls preload='metadata' src='{escape(_as_file_uri(clip.local_clip_path))}' class='clip-player'></video>"
                "<div class='kv compact'>"
                f"<div class='label'>Clip URL</div><div>{escape(clip.clip_video_url)}</div>"
                f"<div class='label'>Clip Path</div><div>{escape(clip.local_clip_path)}</div>"
                f"<div class='label'>Padding</div><div>{clip.actual_padding_start:.3f}s / {clip.actual_padding_end:.3f}s</div>"
                "</div>"
            )
        elif skipped is not None:
            clip_block = (
                "<div class='warning-box'>"
                f"<strong>{escape(skipped.reason)}</strong><br/>{escape(skipped.error_detail)}"
                "</div>"
            )

        enrichment_block = "<p class='muted'>No Agent A cut enrichment available.</p>"
        if enrichment is not None:
            enrichment_block = (
                "<div class='kv compact'>"
                f"<div class='label'>Camera Angle</div><div>{escape(enrichment.camera_angle)}</div>"
                f"<div class='label'>Transition</div><div>{escape(enrichment.transition_type)}</div>"
                f"<div class='label'>Notes</div><div>{escape(enrichment.camera_notes)}</div>"
                "</div>"
            )

        pair_sections.append(
            "<section class='pair-card'>"
            f"<h3>{escape(cut.id)}</h3>"
            "<div class='pair-grid'>"
            "<div class='card'>"
            "<h4>Segment</h4>"
            f"{clip_block}"
            "</div>"
            "<div class='card'>"
            "<h4>Cut + Agent A</h4>"
            "<div class='kv compact'>"
            f"<div class='label'>Start</div><div>{_format_seconds(cut.start_time)}</div>"
            f"<div class='label'>End</div><div>{_format_seconds(cut.end_time)}</div>"
            f"<div class='label'>Duration</div><div>{_format_seconds(cut.end_time - cut.start_time)}</div>"
            "</div>"
            f"{enrichment_block}"
            "</div>"
            "</div>"
            "</section>"
        )

    validation_block = "<p>PASS - No Agent A validation issues detected.</p>"
    if validation_issues:
        validation_items = "".join(f"<li>{escape(issue)}</li>" for issue in validation_issues)
        validation_block = "<p>FAIL - Agent A validation issues detected.</p><ul>" + validation_items + "</ul>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(report_title)}</title>
  <style>
    body {{
      margin: 0;
      background: #f6f7fb;
      color: #1c2333;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .wrap {{
      max-width: 1180px;
      margin: 24px auto;
      padding: 0 16px 32px;
    }}
    .hero, .card, .pair-card {{
      background: #fff;
      border: 1px solid #d8dcea;
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 12px;
    }}
    .source-player, .clip-player {{
      width: 100%;
      border-radius: 8px;
      border: 1px solid #d8dcea;
      background: #000;
    }}
    .pair-grid {{
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 12px;
    }}
    .kv {{
      display: grid;
      grid-template-columns: 200px 1fr;
      gap: 8px 10px;
    }}
    .kv.compact {{
      grid-template-columns: 120px 1fr;
      font-size: 14px;
      margin-top: 10px;
    }}
    .label {{
      color: #5e6575;
    }}
    .warning-box {{
      border: 1px solid #efd5a8;
      background: #fff6e4;
      color: #74520f;
      border-radius: 8px;
      padding: 10px;
    }}
    .muted {{
      color: #5e6575;
    }}
    h1, h2, h3, h4 {{
      margin: 0 0 12px 0;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>{escape(report_title)}</h1>
      <video controls preload="metadata" src="{escape(_as_file_uri(metadata.video_path))}" class="source-player"></video>
      <div class="kv" style="margin-top: 12px;">
        <div class="label">Source Video</div><div>{escape(metadata.video_path)}</div>
        <div class="label">Video URL</div><div>{escape(preprocessing.video_url)}</div>
        <div class="label">Duration</div><div>{metadata.duration_seconds:.3f}s</div>
        <div class="label">Resolution</div><div>{metadata.width} x {metadata.height}</div>
        <div class="label">Cut Count</div><div>{len(preprocessing.cuts)}</div>
        <div class="label">Agent A Enrichments</div><div>{len(agent_a_output.response.cut_enrichments)}</div>
        <div class="label">Segment Clips</div><div>{len(segment_prep.clips)}</div>
        <div class="label">Skipped Cuts</div><div>{len(segment_prep.skipped_cuts)}</div>
      </div>
    </section>
    <section class="card">
      <h2>Agent A Validation</h2>
      {validation_block}
    </section>
    <section class="card">
      <h2>Pairwise Cut Review</h2>
      <p class="muted">Each cut is paired with its Agent A cut enrichment and the generated segment clip.</p>
    </section>
    {''.join(pair_sections)}
  </div>
</body>
</html>
"""


def write_stage_03_04_report(
    preprocessing: PreprocessingResult,
    agent_a_output: AgentARuntimeOutput,
    segment_prep: SegmentPrepResult,
    output_path: Path,
    *,
    title: str | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        build_stage_03_04_report_html(
            preprocessing,
            agent_a_output,
            segment_prep,
            title=title,
        ),
        encoding="utf-8",
    )
    return output_path
