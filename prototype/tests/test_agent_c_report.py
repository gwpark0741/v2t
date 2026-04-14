from __future__ import annotations

from pathlib import Path

from v2t_prototype.agent_c_report import build_agent_c_report_html, write_agent_c_report
from v2t_prototype.models import (
    AgentCResult,
    OnsetEvent,
    PipelineResult,
    Track,
    TrackManifest,
    UnresolvedUnknown,
    WarningItem,
)


def _result() -> AgentCResult:
    return AgentCResult(
        pipeline_result=PipelineResult(
            track_manifest=TrackManifest(
                tracks=[
                    Track(
                        track_id="char_001__foley__tile_floor",
                        track_type="sfx",
                        source_entity_id="char_001",
                        interaction_type="foley",
                        sound_description="tiled footsteps",
                        surface_context_summary="tile floor",
                        events=[
                            OnsetEvent(type="onset", timestamp=0.2),
                            OnsetEvent(type="onset", timestamp=0.4),
                        ],
                    )
                ]
            ),
            unresolved_unknowns=[
                UnresolvedUnknown(
                    unknown_id="UNKNOWN_1",
                    cut_id="CUT_001",
                    observed_visual_description="blurred tool motion",
                    interaction_type="hard_effect",
                    sound_description="metal tap",
                )
            ],
            warnings=[
                WarningItem(
                    code="AGENT_C_PIPELINE_VALIDATION_WARNING",
                    severity="warning",
                    message="Pipeline validation reported a Stage 06 warning.",
                    context={"issue": "misc warning"},
                )
            ],
        ),
        surface_judgments=[],
        merge_group_count=1,
        flash_call_count=0,
    )


def test_build_agent_c_report_html_contains_key_fields():
    html = build_agent_c_report_html(_result(), title="Agent C Report")

    assert "Agent C Report" in html
    assert "char_001__foley__tile_floor" in html
    assert "tiled footsteps" in html
    assert "UNKNOWN_1" in html
    assert "AGENT_C_PIPELINE_VALIDATION_WARNING" in html
    assert "No surface judgments recorded." in html
    assert "Merge Group Count" in html


def test_write_agent_c_report_writes_html_file(tmp_path: Path):
    output_path = tmp_path / "agent_c_report.html"
    written = write_agent_c_report(
        _result(),
        output_path,
        title="Saved Agent C Report",
    )

    assert written == output_path
    content = output_path.read_text(encoding="utf-8")
    assert "Saved Agent C Report" in content
    assert "<html" in content
