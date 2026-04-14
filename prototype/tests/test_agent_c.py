from __future__ import annotations

from typing import Any
from types import SimpleNamespace

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
    assert result.cache_hit_count == 0
    assert result.total_flash_latency_ms == 0.0
    assert result.per_call_flash_latency_ms == []
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
        "AGENT_C_PIPELINE_VALIDATION_WARNING",
    ]


def test_run_agent_c_keeps_duplicate_unresolved_occurrences_without_warning():
    result = run_agent_c(
        _agent_b_result(
            cut_outputs=[
                AgentBCutOutput(
                    cut_id="CUT_003",
                    raw_response_text="{}",
                    actions=[
                        Action(
                            action_id="act_unknown_001",
                            cut_id="CUT_003",
                            primary_source_id="UNKNOWN_CHARACTER_CUT003_1",
                            unknown_resolution=UnknownResolution(
                                suggestion="UNRESOLVED",
                                reason="opponent is off-screen",
                            ),
                            interaction_type="hard_effect",
                            sound_description="opponent paddle hit",
                            surface_context="plastic on rubber",
                            observed_visual_description="The ball is returned from off-screen.",
                            event=OnsetEvent(type="onset", timestamp=1.4),
                            boundary_flag=False,
                        ),
                        Action(
                            action_id="act_unknown_002",
                            cut_id="CUT_003",
                            primary_source_id="UNKNOWN_CHARACTER_CUT003_1",
                            unknown_resolution=UnknownResolution(
                                suggestion="UNRESOLVED",
                                reason="opponent is still off-screen",
                            ),
                            interaction_type="hard_effect",
                            sound_description="opponent paddle hit again",
                            surface_context="plastic on rubber",
                            observed_visual_description="The ball is returned from off-screen for a second time.",
                            event=OnsetEvent(type="onset", timestamp=2.62),
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

    assert [item.unknown_id for item in result.pipeline_result.unresolved_unknowns] == [
        "UNKNOWN_CHARACTER_CUT003_1",
        "UNKNOWN_CHARACTER_CUT003_1",
    ]
    assert [item.code for item in result.pipeline_result.warnings] == []


def test_run_agent_c_marks_empty_result_as_warning():
    result = run_agent_c(
        _agent_b_result(cut_outputs=[]),
        _entity_registry(),
    )

    assert result.pipeline_result.track_manifest.tracks == []
    assert result.pipeline_result.unresolved_unknowns == []
    assert [item.code for item in result.pipeline_result.warnings] == ["AGENT_C_EMPTY_RESULT"]


def test_run_agent_c_records_surface_judgments_and_flash_calls():
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
                            sound_description="ball hits table",
                            surface_context="glass table",
                            observed_visual_description="ball bounces on a table",
                            event=OnsetEvent(type="onset", timestamp=0.3),
                            boundary_flag=False,
                        ),
                        Action(
                            action_id="act_002",
                            cut_id="CUT_001",
                            primary_source_id="obj_001",
                            interaction_type="hard_effect",
                            sound_description="ball hits table again",
                            surface_context="wood composite table",
                            observed_visual_description="ball bounces on the same table",
                            event=OnsetEvent(type="onset", timestamp=0.6),
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
                    '{"result":"COMPATIBLE","reason":"Same table surface."}',
                    _usage_metadata(prompt_token_count=120, candidates_token_count=40),
                )
            ]
        ),
    )

    assert result.flash_call_count == 1
    assert result.cache_hit_count == 0
    assert result.total_flash_latency_ms >= 0.0
    assert len(result.per_call_flash_latency_ms) == 1
    assert result.flash_usage.prompt_token_count == 120
    assert result.flash_usage.candidates_token_count == 40
    assert result.flash_usage.total_token_count == 160
    assert result.estimated_flash_cost_usd == pytest.approx(0.000136)
    assert len(result.surface_judgments) == 1
    assert result.surface_judgments[0].source == "flash"
    assert result.surface_judgments[0].model == "gemini-2.5-flash"
    assert len(result.pipeline_result.track_manifest.tracks) == 1


def test_run_agent_c_surfaces_flash_parse_errors_as_warnings():
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
                            sound_description="ball hits glass",
                            surface_context="glass table",
                            observed_visual_description="ball bounces on the left side",
                            event=OnsetEvent(type="onset", timestamp=0.3),
                            boundary_flag=False,
                        ),
                        Action(
                            action_id="act_002",
                            cut_id="CUT_001",
                            primary_source_id="obj_001",
                            interaction_type="hard_effect",
                            sound_description="ball hits wood",
                            surface_context="wood composite table",
                            observed_visual_description="ball bounces on the right side",
                            event=OnsetEvent(type="onset", timestamp=0.6),
                            boundary_flag=False,
                        ),
                    ],
                    validation_issues=[],
                    model="gemini-2.5-pro",
                )
            ]
        ),
        _entity_registry(),
        flash_client=_FakeClient(responses=["not-json"]),
    )

    assert result.flash_call_count == 1
    assert result.cache_hit_count == 0
    assert len(result.per_call_flash_latency_ms) == 1
    assert result.surface_judgments[0].source == "flash_error"
    assert [item.code for item in result.pipeline_result.warnings] == [
        "SURFACE_FLASH_PARSE_ERROR"
    ]
    assert len(result.pipeline_result.track_manifest.tracks) == 2
