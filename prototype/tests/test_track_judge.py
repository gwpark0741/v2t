from __future__ import annotations

from types import SimpleNamespace

from v2t_prototype.gemini_client import GeminiGenerationParams
from v2t_prototype.models import Action, OnsetEvent
from v2t_prototype.track_judge import TrackJudge


class _FakeResponse:
    def __init__(self, text: str, usage_metadata=None):
        self.text = text
        self.usage_metadata = usage_metadata


class _FakeModels:
    def __init__(self, responses=None, exc: Exception | None = None):
        self._responses = list(responses or [])
        self._exc = exc
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._responses.pop(0)


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


def _action(action_id: str, *, cut_id: str = "CUT_001", sound_description: str = "soft step") -> Action:
    return Action(
        action_id=action_id,
        cut_id=cut_id,
        primary_source_id="fighter_footstep",
        interaction_type="sfx",
        sound_description=sound_description,
        observed_visual_description="player moves forward",
        event=OnsetEvent(type="onset", timestamp=0.2),
        boundary_flag=False,
    )


def test_track_judge_single_action_short_circuits_without_llm():
    judge = TrackJudge(flash_client=_FakeClient())

    groups = judge.judge_group([_action("act_001")], "fighter_footstep", "sfx", "onset")

    assert len(groups) == 1
    assert [action.action_id for action in groups[0]] == ["act_001"]
    assert judge.llm_call_count == 0
    assert judge.get_judgments()[0].source == "single_action"
    assert judge.get_judgments()[0].model is None


def test_track_judge_payload_includes_cut_id_and_returns_llm_groups():
    response = _FakeResponse(
        '{"groups":[{"action_ids":["act_001","act_002"],"reason":"same repeating footstep"}]}',
        usage_metadata=_usage_metadata(prompt_token_count=100, candidates_token_count=20),
    )
    client = _FakeClient(responses=[response])
    judge = TrackJudge(flash_client=client, temperature=0.0)

    groups = judge.judge_group(
        [_action("act_001", cut_id="CUT_001"), _action("act_002", cut_id="CUT_003")],
        "fighter_footstep",
        "sfx",
        "onset",
    )

    assert len(groups) == 1
    assert [action.action_id for action in groups[0]] == ["act_001", "act_002"]
    assert judge.llm_call_count == 1
    assert judge.llm_usage.total_token_count == 120
    payload = client.models.calls[0]["contents"]
    assert '"cut_id": "CUT_001"' in payload
    assert '"cut_id": "CUT_003"' in payload
    assert judge.get_judgments()[0].source == "llm"
    system_instruction = client.models.calls[0]["config"].system_instruction
    assert "Use cut_id to understand temporal context across the video." in system_instruction
    assert "Merge into one track." in system_instruction
    assert "When uncertain, always merge." in system_instruction
    assert client.models.calls[0]["config"].temperature == 0.0


def test_track_judge_accepts_generation_params():
    response = _FakeResponse(
        '{"groups":[{"action_ids":["act_001","act_002"],"reason":"same repeating footstep"}]}'
    )
    client = _FakeClient(responses=[response])
    judge = TrackJudge(
        flash_client=client,
        generation_params=GeminiGenerationParams(
            temperature=0.2,
            top_p=0.8,
            top_k=32,
            seed=42,
            max_output_tokens=2048,
        ),
    )

    judge.judge_group(
        [_action("act_001"), _action("act_002")],
        "fighter_footstep",
        "sfx",
        "onset",
    )

    config = client.models.calls[0]["config"]
    assert config.temperature == 0.2
    assert config.top_p == 0.8
    assert config.top_k == 32
    assert config.seed == 42
    assert config.max_output_tokens == 2048


def test_track_judge_falls_back_on_parse_error():
    client = _FakeClient(responses=[_FakeResponse("not-json")])
    judge = TrackJudge(flash_client=client)

    groups = judge.judge_group([_action("act_001"), _action("act_002")], "fighter_footstep", "sfx", "onset")

    assert [[action.action_id for action in group] for group in groups] == [["act_001"], ["act_002"]]
    assert judge.get_judgments()[0].source == "llm_error"
    assert [warning.code for warning in judge.get_warnings()] == ["TRACK_JUDGE_PARSE_ERROR"]


def test_track_judge_falls_back_on_action_id_mismatch():
    client = _FakeClient(
        responses=[_FakeResponse('{"groups":[{"action_ids":["act_001"],"reason":"partial"}]}')]
    )
    judge = TrackJudge(flash_client=client)

    groups = judge.judge_group([_action("act_001"), _action("act_002")], "fighter_footstep", "sfx", "onset")

    assert [[action.action_id for action in group] for group in groups] == [["act_001"], ["act_002"]]
    assert [warning.code for warning in judge.get_warnings()] == ["TRACK_JUDGE_ID_MISMATCH"]


def test_track_judge_falls_back_on_api_error():
    judge = TrackJudge(flash_client=_FakeClient(exc=RuntimeError("network down")))

    groups = judge.judge_group([_action("act_001"), _action("act_002")], "fighter_footstep", "sfx", "onset")

    assert [[action.action_id for action in group] for group in groups] == [["act_001"], ["act_002"]]
    assert judge.llm_call_count == 1
    assert [warning.code for warning in judge.get_warnings()] == ["TRACK_JUDGE_API_ERROR"]
