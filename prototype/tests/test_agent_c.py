from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

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


class _FakeResponse:
    def __init__(self, text: str, usage_metadata=None):
        self.text = text
        self.usage_metadata = usage_metadata


class _FakeModels:
    def __init__(self, responses=None, exc: Exception | None = None):
        self._responses = list(responses or [])
        self._exc = exc

    def generate_content(self, **kwargs: Any) -> _FakeResponse:
        if self._exc is not None:
            raise self._exc
        payload = self._responses.pop(0)
        if isinstance(payload, tuple):
            return _FakeResponse(payload[0], usage_metadata=payload[1])
        return _FakeResponse(payload)


class _FakeClient:
    def __init__(self, responses=None, exc: Exception | None = None):
        self.models = _FakeModels(responses=responses, exc=exc)


def _usage_metadata(
    *,
    prompt_token_count: int = 0,
    candidates_token_count: int = 0,
    total_token_count: int | None = None,
):
    return SimpleNamespace(
        prompt_token_count=prompt_token_count,
        candidates_token_count=candidates_token_count,
        total_token_count=total_token_count if total_token_count is not None else prompt_token_count + candidates_token_count,
        cached_content_token_count=0,
        thoughts_token_count=0,
        tool_use_prompt_token_count=0,
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


def test_run_agent_c_synthesizes_tracks_and_records_track_group_judgments():
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
                            interaction_type="sfx",
                            sound_description="soft sneaker step on tile",
                            observed_visual_description="player steps forward",
                            event=OnsetEvent(type="onset", timestamp=0.2),
                            boundary_flag=False,
                        ),
                        Action(
                            action_id="act_002",
                            cut_id="CUT_001",
                            primary_source_id="char_001",
                            interaction_type="sfx",
                            sound_description="soft sneaker step on tile",
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
        flash_client=_FakeClient(
            responses=[
                (
                    '{"groups":[{"action_ids":["act_001","act_002"],"reason":"same repeating footstep"}]}',
                    _usage_metadata(prompt_token_count=100, candidates_token_count=20),
                )
            ]
        ),
    )

    assert result.llm_call_count == 1
    assert result.total_llm_latency_ms >= 0.0
    assert result.llm_usage.total_token_count == 120
    assert result.merge_group_count == 1
    assert len(result.track_group_judgments) == 1
    assert result.track_group_judgments[0].source == "llm"
    assert len(result.pipeline_result.track_manifest.tracks) == 1
    assert result.pipeline_result.track_manifest.tracks[0].track_id == "char_001__sfx__onset"
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
                            interaction_type="ambience",
                            sound_description="steady room tone",
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

    assert result.llm_call_count == 0
    assert result.pipeline_result.track_manifest.tracks[0].track_type == "ambience"
    assert result.pipeline_result.track_manifest.tracks[0].track_id == "amb_001__ambience"


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
                            primary_source_id="UNKNOWN_OBJECT_CUT001_1",
                            unknown_resolution=UnknownResolution(
                                suggestion="UNRESOLVED",
                                reason="source is occluded",
                            ),
                            interaction_type="sfx",
                            sound_description="metal clash",
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
    assert result.pipeline_result.unresolved_unknowns[0].unknown_id == "UNKNOWN_OBJECT_CUT001_1"


def test_run_agent_c_converts_validation_issues_to_warnings(monkeypatch):
    def fake_validate_pipeline_result(_result):
        return [
            "duplicate track_id trk_001",
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
                            primary_source_id="amb_001",
                            interaction_type="ambience",
                            sound_description="steady room tone",
                            observed_visual_description="wide room shot",
                            event=ContinuousEvent(type="continuous", start_time=0.0, end_time=1.0),
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

    assert [warning.code for warning in result.pipeline_result.warnings] == [
        "AGENT_C_DUPLICATE_TRACK_ID",
        "AGENT_C_PIPELINE_VALIDATION_WARNING",
    ]


def test_run_agent_c_records_llm_error_fallback_warning():
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
                            interaction_type="sfx",
                            sound_description="first mechanical click",
                            observed_visual_description="device advances",
                            event=OnsetEvent(type="onset", timestamp=0.1),
                            boundary_flag=False,
                        ),
                        Action(
                            action_id="act_002",
                            cut_id="CUT_001",
                            primary_source_id="obj_001",
                            interaction_type="sfx",
                            sound_description="second mechanical click",
                            observed_visual_description="device advances again",
                            event=OnsetEvent(type="onset", timestamp=0.3),
                            boundary_flag=False,
                        ),
                    ],
                    validation_issues=[],
                    model="gemini-2.5-pro",
                )
            ]
        ),
        _entity_registry(),
        flash_client=_FakeClient(exc=RuntimeError("network down")),
    )

    assert result.llm_call_count == 1
    assert [warning.code for warning in result.pipeline_result.warnings] == ["TRACK_JUDGE_API_ERROR"]
    assert len(result.pipeline_result.track_manifest.tracks) == 2
    assert result.track_group_judgments[0].source == "llm_error"


def test_run_agent_c_separates_onset_and_continuous_tracks_for_same_source():
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
                            interaction_type="sfx",
                            sound_description="single impact",
                            observed_visual_description="tool hits surface",
                            event=OnsetEvent(type="onset", timestamp=0.2),
                            boundary_flag=False,
                        ),
                        Action(
                            action_id="act_002",
                            cut_id="CUT_001",
                            primary_source_id="obj_001",
                            interaction_type="sfx",
                            sound_description="steady rolling noise",
                            observed_visual_description="tool keeps sliding",
                            event=ContinuousEvent(type="continuous", start_time=0.3, end_time=0.9),
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

    track_ids = {track.track_id for track in result.pipeline_result.track_manifest.tracks}
    assert track_ids == {"obj_001__sfx__onset", "obj_001__sfx__continuous"}
