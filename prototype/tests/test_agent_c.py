from __future__ import annotations

from v2t_prototype.agent_c import run_agent_c
from v2t_prototype.models import (
    Action,
    AgentBAllCutsResult,
    AgentBCutOutput,
    AmbienceSource,
    Character,
    ContinuousEvent,
    EntityRegistry,
    Interval,
    KeyObject,
    OnsetEvent,
    UnknownResolution,
)


def _entity_registry() -> EntityRegistry:
    return EntityRegistry(
        characters=[
            Character(
                id="char_001",
                label="Player",
                visual_description="Player in motion",
                entry_exit_intervals=[Interval(start_time=0.0, end_time=8.0)],
                audibility="likely_audible",
            )
        ],
        key_objects=[
            KeyObject(
                id="obj_001",
                label="Paddle",
                visual_description="Ping pong paddle",
                material="wood",
                surface="rubber",
                has_mechanism=False,
                audibility="audible",
            )
        ],
        ambience_sources=[
            AmbienceSource(
                id="amb_001",
                label="Gym",
                space_description="Indoor gym",
                distance_profile="mid",
                tonal_quality="bright",
            )
        ],
    )


def _agent_b_result(*, cut_outputs: list[AgentBCutOutput]) -> AgentBAllCutsResult:
    total_actions = sum(len(cut_output.actions) for cut_output in cut_outputs)
    unresolved_count = sum(
        1
        for cut_output in cut_outputs
        for action in cut_output.actions
        if action.primary_source_id.startswith("UNKNOWN_")
        and action.unknown_resolution is not None
        and action.unknown_resolution.suggestion == "UNRESOLVED"
    )
    return AgentBAllCutsResult(
        cut_outputs=cut_outputs,
        skipped_cut_ids=[],
        failed_cut_ids=[],
        total_actions=total_actions,
        unresolved_count=unresolved_count,
        reassigned_count=0,
        warnings=[],
    )


def test_run_agent_c_synthesizes_tracks_and_defaults_surface_judgments():
    result = run_agent_c(
        _agent_b_result(
            cut_outputs=[
                AgentBCutOutput(
                    cut_id="CUT_001",
                    raw_response_text="{}",
                    actions=[
                        Action(
                            action_id="act_001",
                            cut_id="CUT_001",
                            primary_source_id="char_001",
                            interaction_type="foley",
                            sound_description="short step",
                            surface_context="tile floor",
                            observed_visual_description="player steps forward",
                            event=OnsetEvent(type="onset", timestamp=0.2),
                            boundary_flag=False,
                        ),
                        Action(
                            action_id="act_002",
                            cut_id="CUT_001",
                            primary_source_id="char_001",
                            interaction_type="foley",
                            sound_description="longer tiled footsteps",
                            surface_context="tile floor",
                            observed_visual_description="player keeps stepping",
                            event=OnsetEvent(type="onset", timestamp=0.4),
                            boundary_flag=False,
                        ),
                    ],
                    validation_issues=[],
                    model="gemini-2.5-pro",
                )
            ]
        ),
        _entity_registry(),
    )

    assert result.flash_call_count == 0
    assert result.surface_judgments == []
    assert result.merge_group_count == 1
    assert len(result.pipeline_result.track_manifest.tracks) == 1
    assert result.pipeline_result.track_manifest.tracks[0].sound_description == "longer tiled footsteps"
    assert result.pipeline_result.warnings == []


def test_run_agent_c_uses_registry_to_mark_ambience_tracks():
    result = run_agent_c(
        _agent_b_result(
            cut_outputs=[
                AgentBCutOutput(
                    cut_id="CUT_001",
                    raw_response_text="{}",
                    actions=[
                        Action(
                            action_id="act_amb_001",
                            cut_id="CUT_001",
                            primary_source_id="amb_001",
                            interaction_type="background",
                            sound_description="room tone",
                            surface_context=None,
                            observed_visual_description="wide room shot",
                            event=ContinuousEvent(
                                type="continuous",
                                start_time=0.0,
                                end_time=2.0,
                            ),
                            boundary_flag=False,
                        )
                    ],
                    validation_issues=[],
                    model="gemini-2.5-pro",
                )
            ]
        ),
        _entity_registry(),
    )

    assert result.pipeline_result.track_manifest.tracks[0].track_type == "ambience"


def test_run_agent_c_preserves_unresolved_unknowns():
    result = run_agent_c(
        _agent_b_result(
            cut_outputs=[
                AgentBCutOutput(
                    cut_id="CUT_001",
                    raw_response_text="{}",
                    actions=[
                        Action(
                            action_id="act_unknown_001",
                            cut_id="CUT_001",
                            primary_source_id="UNKNOWN_OBJECT_001",
                            unknown_resolution=UnknownResolution(
                                suggestion="UNRESOLVED",
                                reason="source is occluded",
                            ),
                            interaction_type="hard_effect",
                            sound_description="metal clash",
                            surface_context="steel",
                            observed_visual_description="objects collide offscreen",
                            event=OnsetEvent(type="onset", timestamp=0.8),
                            boundary_flag=False,
                        )
                    ],
                    validation_issues=[],
                    model="gemini-2.5-pro",
                )
            ]
        ),
        _entity_registry(),
    )

    assert result.pipeline_result.track_manifest.tracks == []
    assert len(result.pipeline_result.unresolved_unknowns) == 1
    assert result.pipeline_result.unresolved_unknowns[0].unknown_id == "UNKNOWN_OBJECT_001"


def test_run_agent_c_converts_validation_issues_to_warnings(monkeypatch):
    def fake_validate_pipeline_result(_result):
        return [
            "duplicate track_id trk_001",
            "duplicate unresolved unknown UNKNOWN_1",
            "misc warning",
        ]

    monkeypatch.setattr("v2t_prototype.agent_c.validate_pipeline_result", fake_validate_pipeline_result)

    result = run_agent_c(
        _agent_b_result(
            cut_outputs=[
                AgentBCutOutput(
                    cut_id="CUT_001",
                    raw_response_text="{}",
                    actions=[
                        Action(
                            action_id="act_001",
                            cut_id="CUT_001",
                            primary_source_id="obj_001",
                            interaction_type="hard_effect",
                            sound_description="sharp tap",
                            surface_context="wood",
                            observed_visual_description="paddle hits table",
                            event=OnsetEvent(type="onset", timestamp=0.3),
                            boundary_flag=False,
                        )
                    ],
                    validation_issues=[],
                    model="gemini-2.5-pro",
                )
            ]
        ),
        _entity_registry(),
    )

    warning_codes = [item.code for item in result.pipeline_result.warnings]
    assert warning_codes == [
        "AGENT_C_DUPLICATE_TRACK_ID",
        "AGENT_C_DUPLICATE_UNRESOLVED_UNKNOWN",
        "AGENT_C_PIPELINE_VALIDATION_WARNING",
    ]


def test_run_agent_c_marks_empty_result_as_warning():
    result = run_agent_c(
        _agent_b_result(cut_outputs=[]),
        _entity_registry(),
    )

    assert result.pipeline_result.track_manifest.tracks == []
    assert result.pipeline_result.unresolved_unknowns == []
    assert [item.code for item in result.pipeline_result.warnings] == ["AGENT_C_EMPTY_RESULT"]
