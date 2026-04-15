from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from v2t_prototype.agent_a_runtime import AgentARuntimeOutput
from v2t_prototype.agent_b import build_agent_b_cut_input
from v2t_prototype.agent_b_runtime import (
    DEFAULT_AGENT_B_MODEL,
    DEFAULT_AGENT_B_SYSTEM_PROMPT,
    run_agent_b_all_cuts_parallel,
    run_agent_b_for_cut,
)
from v2t_prototype.models import (
    Action,
    AgentARequest,
    AgentAResponse,
    AgentBCutOutput,
    AmbienceSource,
    Character,
    ContinuousEvent,
    Cut,
    EntityRegistry,
    Interval,
    KeyObject,
    SegmentClip,
    SegmentPrepResult,
)


def _entity_registry() -> EntityRegistry:
    return EntityRegistry(
        characters=[
            Character(
                id="char_001",
                label="Fighter",
                visual_description="Armored fighter",
                entry_exit_intervals=[Interval(start_time=0.0, end_time=4.0)],
                audibility="likely_audible",
            )
        ],
        key_objects=[
            KeyObject(
                id="obj_001",
                label="Sword",
                visual_description="Steel sword",
                material="steel",
                surface="polished",
                has_mechanism=False,
                audibility="audible",
            )
        ],
        ambience_sources=[
            AmbienceSource(
                id="amb_001",
                label="Yard",
                space_description="Open yard",
                distance_profile="mid",
                tonal_quality="dry",
            )
        ],
    )


def _cut(cut_id: str = "CUT_001", start: float = 0.0, end: float = 4.0) -> Cut:
    return Cut(id=cut_id, start_time=start, end_time=end)


def _clip(cut_id: str = "CUT_001") -> SegmentClip:
    return SegmentClip(
        cut_id=cut_id,
        local_clip_path=f"/tmp/{cut_id}.mp4",
        clip_video_url=f"https://example.com/{cut_id.lower()}",
        clip_gemini_file_name=f"files/{cut_id.lower()}",
        clip_video_mime_type="video/mp4",
    )


def _agent_a_output() -> AgentARuntimeOutput:
    request = AgentARequest(
        video_url="https://example.com/full",
        video_mime_type="video/mp4",
        video_metadata={
            "video_path": "/tmp/source.mp4",
            "fps": 24.0,
            "frame_count": 240,
            "duration_seconds": 8.0,
            "width": 1280,
            "height": 720,
        },
        cuts=[_cut("CUT_001", 0.0, 4.0), _cut("CUT_002", 4.0, 8.0)],
    )
    response = AgentAResponse(entity_registry=_entity_registry())
    return AgentARuntimeOutput(
        request=request,
        response=response,
        raw_response_text=response.model_dump_json(),
    )


def _make_runtime_input():
    return build_agent_b_cut_input(_clip(), _cut(), _entity_registry())


def _valid_action_dict(**overrides):
    payload = {
        "action_id": "act_CUT_001_001",
        "cut_id": "CUT_001",
        "primary_source_id": "obj_001",
        "interaction_type": "sfx",
        "sound_description": "metal sword clash with a sharp ring",
        "observed_visual_description": "two swords collide",
        "event": {
            "type": "continuous",
            "start_time": 0.5,
            "end_time": 1.5,
        },
        "boundary_flag": True,
    }
    payload.update(overrides)
    return payload


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


def _mock_client_with_texts(*texts: str) -> Mock:
    client = Mock()
    client.models.generate_content.side_effect = [
        SimpleNamespace(text=text, usage_metadata=_usage_metadata()) for text in texts
    ]
    return client


def _mock_client_with_payloads(*payloads: tuple[str, object]) -> Mock:
    client = Mock()
    client.models.generate_content.side_effect = [
        SimpleNamespace(text=text, usage_metadata=usage)
        for text, usage in payloads
    ]
    return client


def test_run_agent_b_for_cut_success_rewrites_reassign_and_forces_boundary_false():
    input_model = _make_runtime_input()
    response_text = json.dumps(
        {
            "actions": [
                _valid_action_dict(
                    action_id="act_CUT_001_001",
                    primary_source_id="UNKNOWN_OBJECT_CUT001_1",
                    unknown_resolution={
                        "suggestion": "REASSIGN_TO_EXISTING",
                        "suggested_entity_id": "obj_001",
                        "reason": "looks like the known sword",
                    },
                )
            ]
        }
    )
    client = _mock_client_with_texts(response_text)

    with patch("v2t_prototype.agent_b_runtime.types.Part.from_uri") as part_from_uri:
        part_from_uri.return_value = SimpleNamespace(content="clip")
        output = run_agent_b_for_cut(input_model, client=client)

    assert output.model == DEFAULT_AGENT_B_MODEL
    assert output.latency_ms >= 0.0
    assert output.usage.total_token_count == 0
    assert output.estimated_cost_usd == 0.0
    assert output.validation_issues == []
    assert len(output.actions) == 1
    assert output.actions[0].primary_source_id == "obj_001"
    assert output.actions[0].boundary_flag is False
    part_from_uri.assert_called_once_with(
        file_uri=input_model.clip_video_url,
        mime_type=input_model.clip_video_mime_type,
    )


def test_run_agent_b_for_cut_normalizes_local_event_time_to_absolute_time():
    input_model = build_agent_b_cut_input(
        _clip("CUT_003"),
        _cut("CUT_003", 3.067, 6.067),
        _entity_registry(),
    )
    response_text = json.dumps(
        {
            "actions": [
                _valid_action_dict(
                    action_id="act_CUT_003_001",
                    cut_id="CUT_003",
                    event={
                        "type": "onset",
                        "timestamp": 0.15,
                    },
                    boundary_flag=False,
                )
            ]
        }
    )
    client = _mock_client_with_texts(response_text)

    with patch("v2t_prototype.agent_b_runtime.types.Part.from_uri") as part_from_uri:
        part_from_uri.return_value = SimpleNamespace(content="clip")
        output = run_agent_b_for_cut(input_model, client=client)

    assert output.validation_issues == []
    assert output.actions[0].event.type == "onset"
    assert output.actions[0].event.timestamp == pytest.approx(3.217)


def test_run_agent_b_for_cut_retries_when_local_event_time_is_out_of_range():
    input_model = _make_runtime_input()
    client = _mock_client_with_texts(
        json.dumps(
            {
                "actions": [
                    _valid_action_dict(
                        boundary_flag=False,
                        event={
                            "type": "onset",
                            "timestamp": 4.5,
                        },
                    )
                ]
            }
        ),
        json.dumps({"actions": [_valid_action_dict(boundary_flag=False)]}),
    )

    with patch("v2t_prototype.agent_b_runtime.types.Part.from_uri") as part_from_uri:
        part_from_uri.return_value = SimpleNamespace(content="clip")
        output = run_agent_b_for_cut(input_model, client=client, max_retries=1)

    assert output.validation_issues == []
    assert len(output.actions) == 1
    assert client.models.generate_content.call_count == 2


def test_run_agent_b_for_cut_accepts_boundary_event_when_cut_duration_rounds_down():
    input_model = build_agent_b_cut_input(
        _clip("CUT_004"),
        _cut("CUT_004", 5.0, 6.433),
        _entity_registry(),
    )
    response_text = json.dumps(
        {
            "actions": [
                _valid_action_dict(
                    action_id="act_CUT_004_001",
                    cut_id="CUT_004",
                    event={
                        "type": "continuous",
                        "start_time": 0.0,
                        "end_time": 1.433,
                    },
                    boundary_flag=False,
                )
            ]
        }
    )
    client = _mock_client_with_texts(response_text)

    with patch("v2t_prototype.agent_b_runtime.types.Part.from_uri") as part_from_uri:
        part_from_uri.return_value = SimpleNamespace(content="clip")
        output = run_agent_b_for_cut(input_model, client=client, max_retries=0)

    assert output.validation_issues == []
    assert len(output.actions) == 1
    assert output.actions[0].event.type == "continuous"
    assert output.actions[0].event.start_time == pytest.approx(5.0)
    assert output.actions[0].event.end_time == pytest.approx(6.433)


def test_agent_b_system_prompt_includes_new_interaction_types_and_examples():
    assert "sfx      — non-vocal foreground sound events" in DEFAULT_AGENT_B_SYSTEM_PROMPT
    assert "voice    — all human vocal sounds" in DEFAULT_AGENT_B_SYSTEM_PROMPT
    assert "ambience — continuous spatial sound with no specific singular foreground source" in DEFAULT_AGENT_B_SYSTEM_PROMPT
    assert "surface_context" not in DEFAULT_AGENT_B_SYSTEM_PROMPT
    assert "Light, steady rain falling on wet city pavement and surfaces." in DEFAULT_AGENT_B_SYSTEM_PROMPT
    assert "Forceful burst of powdery snow, a quick whoosh, and muffled landing." in DEFAULT_AGENT_B_SYSTEM_PROMPT
    assert "acoustically equivalent" in DEFAULT_AGENT_B_SYSTEM_PROMPT
    assert "timestamps and cuts." in DEFAULT_AGENT_B_SYSTEM_PROMPT
    assert "Do not rewrite the description just because the timing changed." in DEFAULT_AGENT_B_SYSTEM_PROMPT
    assert "All event timestamps must be relative to THIS clip." in DEFAULT_AGENT_B_SYSTEM_PROMPT
    assert "The clip always starts at 0.0 seconds." in DEFAULT_AGENT_B_SYSTEM_PROMPT
    assert "Do NOT use full-video absolute timestamps." in DEFAULT_AGENT_B_SYSTEM_PROMPT


def test_run_agent_b_for_cut_retries_after_parse_error():
    input_model = _make_runtime_input()
    client = _mock_client_with_texts(
        "{not-json",
        json.dumps({"actions": [_valid_action_dict(boundary_flag=False)]}),
    )

    with patch("v2t_prototype.agent_b_runtime.types.Part.from_uri") as part_from_uri:
        part_from_uri.return_value = SimpleNamespace(content="clip")
        output = run_agent_b_for_cut(input_model, client=client, max_retries=2)

    assert output.validation_issues == []
    assert len(output.actions) == 1
    assert client.models.generate_content.call_count == 2


def test_run_agent_b_for_cut_retries_after_retryable_generation_error():
    input_model = _make_runtime_input()
    client = Mock()
    client.models.generate_content.side_effect = [
        RuntimeError("503 UNAVAILABLE"),
        SimpleNamespace(
            text=json.dumps({"actions": [_valid_action_dict(boundary_flag=False)]}),
            usage_metadata=_usage_metadata(),
        ),
    ]

    with (
        patch("v2t_prototype.agent_b_runtime.types.Part.from_uri") as part_from_uri,
        patch("v2t_prototype.agent_b_runtime.time.sleep") as sleep_mock,
    ):
        part_from_uri.return_value = SimpleNamespace(content="clip")
        output = run_agent_b_for_cut(input_model, client=client, max_retries=1)

    assert output.validation_issues == []
    assert len(output.actions) == 1
    assert client.models.generate_content.call_count == 2
    sleep_mock.assert_called_once()


def test_run_agent_b_for_cut_raises_non_retryable_generation_error():
    input_model = _make_runtime_input()
    client = Mock()
    client.models.generate_content.side_effect = RuntimeError("400 INVALID_ARGUMENT")

    with patch("v2t_prototype.agent_b_runtime.types.Part.from_uri") as part_from_uri:
        part_from_uri.return_value = SimpleNamespace(content="clip")
        with pytest.raises(RuntimeError, match="400 INVALID_ARGUMENT"):
            run_agent_b_for_cut(input_model, client=client, max_retries=2)


def test_run_agent_b_for_cut_normalizes_onset_event_type_variant():
    input_model = _make_runtime_input()
    response_text = json.dumps(
        {
            "actions": [
                _valid_action_dict(
                    boundary_flag=False,
                    event={
                        "type": "OnsetEvent",
                        "timestamp": 0.8,
                    },
                )
            ]
        }
    )
    client = _mock_client_with_texts(response_text)

    with patch("v2t_prototype.agent_b_runtime.types.Part.from_uri") as part_from_uri:
        part_from_uri.return_value = SimpleNamespace(content="clip")
        output = run_agent_b_for_cut(input_model, client=client)

    assert output.validation_issues == []
    assert len(output.actions) == 1
    assert output.actions[0].event.type == "onset"
    assert output.actions[0].event.timestamp == 0.8


def test_run_agent_b_for_cut_normalizes_continuous_event_type_variant():
    input_model = _make_runtime_input()
    response_text = json.dumps(
        {
            "actions": [
                _valid_action_dict(
                    boundary_flag=False,
                    event={
                        "type": "continuous_event",
                        "start_time": 0.5,
                        "end_time": 1.5,
                    },
                )
            ]
        }
    )
    client = _mock_client_with_texts(response_text)

    with patch("v2t_prototype.agent_b_runtime.types.Part.from_uri") as part_from_uri:
        part_from_uri.return_value = SimpleNamespace(content="clip")
        output = run_agent_b_for_cut(input_model, client=client)

    assert output.validation_issues == []
    assert len(output.actions) == 1
    assert output.actions[0].event.type == "continuous"


def test_run_agent_b_for_cut_retries_when_event_type_remains_invalid_after_normalization():
    input_model = _make_runtime_input()
    client = _mock_client_with_texts(
        json.dumps(
            {
                "actions": [
                    _valid_action_dict(
                        boundary_flag=False,
                        event={
                            "type": "impact",
                            "timestamp": 0.5,
                        },
                    )
                ]
            }
        )
    )

    with patch("v2t_prototype.agent_b_runtime.types.Part.from_uri") as part_from_uri:
        part_from_uri.return_value = SimpleNamespace(content="clip")
        output = run_agent_b_for_cut(input_model, client=client, max_retries=0)

    assert output.actions == []
    assert output.validation_issues == ["AGENT_B_RESPONSE_PARSE_ERROR"]
    assert client.models.generate_content.call_count == 1


def test_run_agent_b_for_cut_accumulates_usage_and_cost_across_retries():
    input_model = _make_runtime_input()
    client = _mock_client_with_payloads(
        (
            "{not-json",
            _usage_metadata(prompt_token_count=1000, candidates_token_count=50),
        ),
        (
            json.dumps({"actions": [_valid_action_dict(boundary_flag=False)]}),
            _usage_metadata(prompt_token_count=900, candidates_token_count=75),
        ),
    )

    with patch("v2t_prototype.agent_b_runtime.types.Part.from_uri") as part_from_uri:
        part_from_uri.return_value = SimpleNamespace(content="clip")
        output = run_agent_b_for_cut(input_model, client=client, max_retries=1)

    assert output.validation_issues == []
    assert output.usage.prompt_token_count == 1900
    assert output.usage.candidates_token_count == 125
    assert output.usage.total_token_count == 2025
    assert output.estimated_cost_usd == pytest.approx(0.003625)


def test_run_agent_b_for_cut_retries_small_validation_issue_then_succeeds():
    input_model = _make_runtime_input()
    first_text = json.dumps(
        {
            "actions": [
                _valid_action_dict(cut_id="CUT_999", boundary_flag=False),
            ]
        }
    )
    second_text = json.dumps({"actions": [_valid_action_dict(boundary_flag=False)]})
    client = _mock_client_with_texts(first_text, second_text)

    with patch("v2t_prototype.agent_b_runtime.types.Part.from_uri") as part_from_uri:
        part_from_uri.return_value = SimpleNamespace(content="clip")
        output = run_agent_b_for_cut(input_model, client=client, max_retries=2)

    assert output.validation_issues == []
    assert len(output.actions) == 1
    assert client.models.generate_content.call_count == 2


def test_run_agent_b_for_cut_returns_empty_actions_when_validation_issues_are_many():
    input_model = _make_runtime_input()
    client = _mock_client_with_texts(
        json.dumps(
            {
                "actions": [
                    _valid_action_dict(
                        action_id="act_CUT_001_001",
                        cut_id="CUT_999",
                        primary_source_id="obj_missing_1",
                        boundary_flag=False,
                    ),
                    _valid_action_dict(
                        action_id="act_CUT_001_001",
                        cut_id="CUT_998",
                        primary_source_id="obj_missing_2",
                        boundary_flag=False,
                    ),
                ]
            }
        )
    )

    with patch("v2t_prototype.agent_b_runtime.types.Part.from_uri") as part_from_uri:
        part_from_uri.return_value = SimpleNamespace(content="clip")
        output = run_agent_b_for_cut(input_model, client=client, max_retries=2)

    assert output.actions == []
    assert len(output.validation_issues) > 3
    assert client.models.generate_content.call_count == 1


def test_run_agent_b_all_cuts_parallel_aggregates_outputs_and_failures():
    segment_prep = SegmentPrepResult(
        clips=[_clip("CUT_001"), _clip("CUT_002")],
        skipped_cuts=[{"cut_id": "CUT_003", "reason": "UPLOAD_FAILURE", "error_detail": "boom"}],
        warnings=[],
    )
    agent_a_output = _agent_a_output()

    outputs = {
        "CUT_001": AgentBCutOutput(
            cut_id="CUT_001",
            raw_response_text="{}",
            actions=[
                Action.model_validate(
                    {
                        "action_id": "act_CUT_001_001",
                        "cut_id": "CUT_001",
                        "primary_source_id": "UNKNOWN_OBJECT_CUT001_1",
                        "unknown_resolution": {
                            "suggestion": "UNRESOLVED",
                            "reason": "not clear",
                        },
                        "interaction_type": "sfx",
                        "sound_description": "metal clang with a bright ring",
                        "observed_visual_description": "swords collide",
                        "event": {
                            "type": "continuous",
                            "start_time": 0.3,
                            "end_time": 0.7,
                        },
                        "boundary_flag": False,
                    }
                )
            ],
            validation_issues=[],
            model=DEFAULT_AGENT_B_MODEL,
        )
    }

    def fake_run_agent_b_for_cut(input_model, **kwargs):
        if input_model.cut_id == "CUT_002":
            raise RuntimeError("boom")
        return outputs[input_model.cut_id]

    with patch("v2t_prototype.agent_b_runtime.run_agent_b_for_cut", side_effect=fake_run_agent_b_for_cut):
        result = asyncio.run(
            run_agent_b_all_cuts_parallel(
                segment_prep,
                agent_a_output,
                client=Mock(),
            )
        )

    assert [output.cut_id for output in result.cut_outputs] == ["CUT_001"]
    assert result.skipped_cut_ids == ["CUT_003"]
    assert result.failed_cut_ids == ["CUT_002"]
    assert result.total_actions == 1
    assert result.unresolved_count == 1
    assert result.reassigned_count == 0
    assert [warning.code for warning in result.warnings] == ["AGENT_B_CUT_RUNTIME_FAILURE"]


def test_run_agent_b_all_cuts_parallel_recovers_failed_cut_sequentially():
    segment_prep = SegmentPrepResult(
        clips=[_clip("CUT_001"), _clip("CUT_002")],
        skipped_cuts=[],
        warnings=[],
    )
    agent_a_output = _agent_a_output()

    outputs = {
        "CUT_001": AgentBCutOutput(
            cut_id="CUT_001",
            raw_response_text="{}",
            actions=[],
            validation_issues=[],
            model=DEFAULT_AGENT_B_MODEL,
        ),
        "CUT_002": AgentBCutOutput(
            cut_id="CUT_002",
            raw_response_text="{}",
            actions=[],
            validation_issues=[],
            model=DEFAULT_AGENT_B_MODEL,
        ),
    }
    call_counts = {"CUT_001": 0, "CUT_002": 0}

    def fake_run_agent_b_for_cut(input_model, **kwargs):
        call_counts[input_model.cut_id] += 1
        if input_model.cut_id == "CUT_002" and call_counts["CUT_002"] == 1:
            raise RuntimeError("503 UNAVAILABLE")
        return outputs[input_model.cut_id]

    with patch("v2t_prototype.agent_b_runtime.run_agent_b_for_cut", side_effect=fake_run_agent_b_for_cut):
        result = asyncio.run(
            run_agent_b_all_cuts_parallel(
                segment_prep,
                agent_a_output,
                client=Mock(),
            )
        )

    assert [output.cut_id for output in result.cut_outputs] == ["CUT_001", "CUT_002"]
    assert result.failed_cut_ids == []
    assert result.warnings == []
    assert call_counts == {"CUT_001": 1, "CUT_002": 2}
