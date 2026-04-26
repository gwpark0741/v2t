from __future__ import annotations

from pathlib import Path

from v2t_prototype.agent_a_runtime import AgentARuntimeOutput
from v2t_prototype.agent_b_report import build_agent_b_report_html, write_agent_b_report
from v2t_prototype.models import (
    Action,
    AgentARequest,
    AgentAResponse,
    AgentBAllCutsResult,
    AgentBCutOutput,
    Ambience,
    ContinuousEvent,
    Cut,
    Entity,
    Entity_Child,
    EntityRegistry,
    Unknown,
    UnknownResolution,
    VideoMetadata,
    WarningItem,
)


def _agent_a_output() -> AgentARuntimeOutput:
    request = AgentARequest(
        video_url="https://example.com/full",
        video_mime_type="video/mp4",
        video_metadata=VideoMetadata(
            video_path="/tmp/source.mp4",
            fps=24.0,
            frame_count=240,
            duration_seconds=8.0,
            width=1280,
            height=720,
        ),
        cuts=[
            Cut(id="CUT_001", start_time=0.0, end_time=4.0),
            Cut(id="CUT_002", start_time=4.0, end_time=8.0),
        ],
    )
    response = AgentAResponse(
        entity_registry=EntityRegistry(
            entities=[
                Entity(
                    id="fighter",
                    label="fighter",
                    children=[
                        Entity_Child(id="fighter_armor", label="fighter armor"),
                    ],
                )
            ],
            ambience=[Ambience(id="yard_wind", label="yard wind")],
            unknowns=[
                Unknown(
                    id="unknown_1",
                    label="unknown 1",
                    visual_description="wrapped item near the fighter",
                )
            ],
        )
    )
    return AgentARuntimeOutput(
        request=request,
        response=response,
        raw_response_text=response.entity_registry.model_dump_json(),
    )


def _result() -> AgentBAllCutsResult:
    return AgentBAllCutsResult(
        cut_outputs=[
            AgentBCutOutput(
                cut_id="CUT_001",
                raw_response_text='{"actions":[{"action_id":"raw_only_marker"}]}',
                actions=[
                    Action.model_validate(
                        {
                            "action_id": "act_CUT_001_001",
                            "cut_id": "CUT_001",
                            "primary_source_id": "UNKNOWN_OBJECT_CUT001_1",
                            "unknown_resolution": UnknownResolution(
                                suggestion="UNRESOLVED",
                                reason="unclear source",
                            ),
                            "interaction_type": "sfx",
                            "sound_description": "metal clash with a bright ring",
                            "observed_visual_description": "swords collide",
                            "event": ContinuousEvent(
                                type="continuous",
                                start_time=0.5,
                                end_time=1.5,
                            ),
                            "boundary_flag": False,
                        }
                    )
                ],
                validation_issues=["AGENT_B_UNKNOWN_SOURCE_ID"],
                model="gemini-2.5-pro",
            )
        ],
        skipped_cut_ids=["CUT_003"],
        failed_cut_ids=["CUT_004"],
        total_actions=1,
        unresolved_count=1,
        reassigned_count=0,
        warnings=[
            WarningItem(
                code="AGENT_B_CUT_RUNTIME_FAILURE",
                severity="warning",
                message="runtime failed",
                context={"cut_id": "CUT_004"},
            )
        ],
    )


def test_build_agent_b_report_html_contains_key_fields():
    html = build_agent_b_report_html(_result(), _agent_a_output(), title="Agent B Report")

    assert "Agent B Report" in html
    assert "CUT_001" in html
    assert "metal clash with a bright ring" in html
    assert "UNKNOWN_OBJECT_CUT001_1" in html
    assert "CUT_003" in html
    assert "CUT_004" in html
    assert "AGENT_B_CUT_RUNTIME_FAILURE" in html
    assert "AGENT_B_UNKNOWN_SOURCE_ID" in html
    assert "raw_only_marker" in html
    assert "continuous 0.500s - 1.500s" in html
    assert "Raw response timestamps below are clip-local." in html
    assert "Entities" in html
    assert "Unknowns" in html
    assert "voice" not in html


def test_write_agent_b_report_writes_html_file(tmp_path: Path):
    output_path = tmp_path / "agent_b_report.html"
    written = write_agent_b_report(
        _result(),
        _agent_a_output(),
        output_path,
        title="Saved Agent B Report",
    )

    assert written == output_path
    content = output_path.read_text(encoding="utf-8")
    assert "Saved Agent B Report" in content
    assert "<html" in content
