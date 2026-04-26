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
    build_agent_b_user_prompt,
    run_agent_b_all_cuts_parallel,
    run_agent_b_for_cut,
)
from v2t_prototype.models import (
    AgentARequest,
    AgentAResponse,
    AgentBCutOutput,
    Ambience,
    Cut,
    Entity,
    Entity_Child,
    EntityRegistry,
    SegmentClip,
    SegmentPrepResult,
    Unknown,
)


def _entity_registry() -> EntityRegistry:
    return EntityRegistry(
        entities=[
            Entity(
                id="samurai",
                label="samurai",
                children=[
                    Entity_Child(id="samurai_armor", label="samurai armor"),
                    Entity_Child(id="samurai_footstep", label="samurai footstep"),
                ],
            ),
            Entity(id="tree", label="tree"),
        ],
        ambience=[Ambience(id="wind", label="wind")],
        unknowns=[
            Unknown(
                id="unknown_1",
                label="unknown 1",
                visual_description="wrapped cylindrical object near the samurai",
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
        raw_response_text=response.entity_registry.model_dump_json(),
    )


def _make_runtime_input():
    return build_agent_b_cut_input(_clip(), _cut(), _entity_registry())


def _valid_action_dict(**overrides):
    payload = {
        "action_id": "act_CUT_001_001",
        "cut_id": "CUT_001",
        "primary_source_id": "samurai_armor",
        "interaction_type": "sfx",
        "sound_description": "metal sword clash with a sharp ring",
        "observed_visual_description": "two armored fighters collide",
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
                        "suggested_entity_id": "samurai_armor",
                        "reason": "matches the registered armor source",
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
    assert output.validation_issues == []
    assert len(output.actions) == 1
    assert output.actions[0].primary_source_id == "samurai_armor"
    assert output.actions[0].interaction_type == "sfx"
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
                    event={"type": "onset", "timestamp": 0.15},
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


def test_run_agent_b_for_cut_derives_interaction_type_from_reassigned_ambience_target():
    input_model = _make_runtime_input()
    response_text = json.dumps(
        {
            "actions": [
                _valid_action_dict(
                    primary_source_id="UNKNOWN_AMBIENCE_CUT001_1",
                    interaction_type="sfx",
                    unknown_resolution={
                        "suggestion": "REASSIGN_TO_EXISTING",
                        "suggested_entity_id": "wind",
                        "reason": "matches the visible wind layer",
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
    assert output.actions[0].primary_source_id == "wind"
    assert output.actions[0].interaction_type == "ambience"


def test_run_agent_b_for_cut_normalizes_interaction_type_for_known_target_without_retry():
    input_model = _make_runtime_input()
    response_text = json.dumps(
        {
            "actions": [
                _valid_action_dict(
                    primary_source_id="wind",
                    interaction_type="sfx",
                    boundary_flag=False,
                )
            ]
        }
    )
    client = _mock_client_with_texts(response_text)

    with patch("v2t_prototype.agent_b_runtime.types.Part.from_uri") as part_from_uri:
        part_from_uri.return_value = SimpleNamespace(content="clip")
        output = run_agent_b_for_cut(input_model, client=client)

    assert client.models.generate_content.call_count == 1
    assert output.validation_issues == []
    assert output.actions[0].primary_source_id == "wind"
    assert output.actions[0].interaction_type == "ambience"


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


def test_agent_b_system_prompt_excludes_voice_and_mentions_leaf_mapping():
    assert "voice" not in DEFAULT_AGENT_B_SYSTEM_PROMPT
    assert "sfx_targets" in DEFAULT_AGENT_B_SYSTEM_PROMPT
    assert "ambience_targets" in DEFAULT_AGENT_B_SYSTEM_PROMPT
    assert "Do not create actions for dialogue" in DEFAULT_AGENT_B_SYSTEM_PROMPT
    assert "Never use an id from unknowns" in DEFAULT_AGENT_B_SYSTEM_PROMPT


def test_build_agent_b_user_prompt_splits_target_sections():
    prompt = build_agent_b_user_prompt(_make_runtime_input())

    assert "sfx_targets (map foreground sound events here):" in prompt
    assert "ambience_targets (map background/environmental layers here):" in prompt
    assert '"id": "samurai_armor"' in prompt
    assert '"id": "tree"' in prompt
    assert '"id": "wind"' in prompt
    assert '"id": "unknown_1"' in prompt


def test_run_agent_b_all_cuts_parallel_aggregates_outputs():
    agent_a_output = _agent_a_output()
    segment_prep = SegmentPrepResult(clips=[_clip("CUT_001"), _clip("CUT_002")])
    cut_outputs = [
        AgentBCutOutput(
            cut_id="CUT_001",
            raw_response_text="{}",
            actions=[],
            validation_issues=[],
            model=DEFAULT_AGENT_B_MODEL,
        ),
        AgentBCutOutput(
            cut_id="CUT_002",
            raw_response_text="{}",
            actions=[],
            validation_issues=[],
            model=DEFAULT_AGENT_B_MODEL,
        ),
    ]

    def fake_run_for_cut(input_model, *, client=None, model=DEFAULT_AGENT_B_MODEL, max_retries=2):
        _ = client, model, max_retries
        for output in cut_outputs:
            if output.cut_id == input_model.cut_id:
                return output
        raise AssertionError("unexpected cut id")

    with patch("v2t_prototype.agent_b_runtime.run_agent_b_for_cut", side_effect=fake_run_for_cut):
        result = asyncio.run(
            run_agent_b_all_cuts_parallel(
                segment_prep=segment_prep,
                agent_a_output=agent_a_output,
                client=Mock(),
            )
        )

    assert [item.cut_id for item in result.cut_outputs] == ["CUT_001", "CUT_002"]
    assert result.total_actions == 0
    assert result.unresolved_count == 0
    assert result.reassigned_count == 0
