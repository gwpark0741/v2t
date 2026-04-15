from __future__ import annotations

from pathlib import Path

from v2t_prototype.agent_c_report import build_agent_c_report_html, write_agent_c_report
from v2t_prototype.models import (
    AgentCResult,
    OnsetEvent,
    PipelineResult,
    TokenUsage,
    Track,
    TrackGroupJudgment,
    TrackGroupResult,
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
                        track_number=1,
                        track_id="char_001__sfx__onset",
                        track_type="sfx",
                        source_entity_id="char_001",
                        sound_description="tiled footsteps",
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
                    interaction_type="sfx",
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
        track_group_judgments=[],
        merge_group_count=1,
        llm_call_count=0,
        total_llm_latency_ms=0.0,
        per_call_llm_latency_ms=[],
        llm_usage=TokenUsage(),
        estimated_llm_cost_usd=0.0,
    )


def test_build_agent_c_report_html_contains_key_fields():
    html = build_agent_c_report_html(_result(), title="Agent C Report")

    assert "Agent C Report" in html
    assert "char_001__sfx__onset" in html
    assert "tiled footsteps" in html
    assert "UNKNOWN_1" in html
    assert "AGENT_C_PIPELINE_VALIDATION_WARNING" in html
    assert "No track group judgments recorded." in html
    assert "Track #" in html
    assert ">1<" in html
    assert "Merge Group Count" in html
    assert "Total LLM Latency (ms)" in html
    assert "No LLM calls recorded." in html


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


def test_build_agent_c_report_html_renders_track_group_judgment_details():
    result = _result().model_copy(
        update={
            "track_group_judgments": [
                TrackGroupJudgment(
                    group_key="char_001__sfx__onset",
                    input_action_ids=["act_001", "act_002"],
                    output_groups=[
                        TrackGroupResult(
                            action_ids=["act_001", "act_002"],
                            reason="same repeating footstep",
                        )
                    ],
                    source="llm_error",
                    model="gemini-2.5-flash",
                )
            ],
            "llm_call_count": 1,
            "total_llm_latency_ms": 12.5,
            "per_call_llm_latency_ms": [12.5],
            "llm_usage": TokenUsage(
                prompt_token_count=120,
                candidates_token_count=20,
                total_token_count=140,
            ),
            "estimated_llm_cost_usd": 0.0001,
        }
    )

    html = build_agent_c_report_html(result, title="Track Group Detail Report")

    assert "Track Group Detail Report" in html
    assert "llm_error" in html
    assert "same repeating footstep" in html
    assert "gemini-2.5-flash" in html
    assert "12.50" in html


def test_build_agent_c_report_html_renders_deterministic_judgment_badge():
    result = _result().model_copy(
        update={
            "track_group_judgments": [
                TrackGroupJudgment(
                    group_key="char_001__voice__onset",
                    input_action_ids=["act_001", "act_002"],
                    output_groups=[
                        TrackGroupResult(
                            action_ids=["act_001", "act_002"],
                            reason="same normalized sound_description",
                        )
                    ],
                    source="deterministic",
                    model=None,
                )
            ]
        }
    )

    html = build_agent_c_report_html(result)

    assert "deterministic" in html
    assert "char_001__voice__onset" in html
