from __future__ import annotations

from types import SimpleNamespace

import pytest

from v2t_prototype.models import Action, OnsetEvent
from v2t_prototype.surface_judge import SurfaceJudge


def _action(
    action_id: str,
    *,
    surface_context: str | None,
    interaction_type: str = "hard_effect",
) -> Action:
    return Action(
        action_id=action_id,
        cut_id="CUT_001",
        primary_source_id="obj_001",
        interaction_type=interaction_type,
        sound_description="impact sound",
        surface_context=surface_context,
        observed_visual_description="object impact",
        event=OnsetEvent(type="onset", timestamp=0.5),
        boundary_flag=False,
    )


class _FakeResponse:
    def __init__(self, text: str, usage_metadata=None):
        self.text = text
        self.usage_metadata = usage_metadata


class _FakeModels:
    def __init__(self, responses=None, exc: Exception | None = None):
        self._responses = list(responses or [])
        self._exc = exc
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
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


def test_surface_judge_marks_both_null_as_compatible():
    judge = SurfaceJudge(flash_client=_FakeClient())

    result = judge.judge(
        _action("act_a", surface_context=None),
        _action("act_b", surface_context=None),
        "hard_effect",
    )

    assert result.result == "COMPATIBLE"
    assert result.source == "null_both"
    assert result.representative_surface is None
    assert judge.flash_call_count == 0
    assert judge.get_judgments()[0].source == "null_both"


def test_surface_judge_uses_non_null_surface_when_one_side_missing():
    judge = SurfaceJudge(flash_client=_FakeClient())

    result = judge.judge(
        _action("act_a", surface_context=None),
        _action("act_b", surface_context="Rubber Floor"),
        "foley",
    )

    assert result.result == "COMPATIBLE"
    assert result.source == "null_one_side"
    assert result.representative_surface == "Rubber Floor"
    assert judge.flash_call_count == 0


def test_surface_judge_matches_after_normalization_without_flash():
    judge = SurfaceJudge(flash_client=_FakeClient())

    result = judge.judge(
        _action("act_a", surface_context="Tile Floor!"),
        _action("act_b", surface_context=" tile floor "),
        "foley",
    )

    assert result.result == "COMPATIBLE"
    assert result.source == "normalize_match"
    assert result.representative_surface == " tile floor "
    assert judge.flash_call_count == 0


def test_surface_judge_reuses_cache_for_reversed_pair():
    client = _FakeClient(
        responses=[
            (
                '{"result":"COMPATIBLE","reason":"Same table surface."}',
                _usage_metadata(prompt_token_count=100, candidates_token_count=20),
            )
        ]
    )
    judge = SurfaceJudge(flash_client=client)
    action_a = _action("act_a", surface_context="glass table")
    action_b = _action("act_b", surface_context="wood composite table")

    first = judge.judge(action_a, action_b, "hard_effect")
    second = judge.judge(action_b, action_a, "hard_effect")

    assert first.source == "flash"
    assert second.source == "cache_hit"
    assert judge.flash_call_count == 1
    assert judge.cache_hit_count == 1
    assert len(judge.per_call_flash_latency_ms) == 1
    assert judge.total_flash_latency_ms >= 0.0
    assert judge.flash_usage.prompt_token_count == 100
    assert judge.flash_usage.candidates_token_count == 20
    assert judge.estimated_flash_cost_usd == pytest.approx(8e-05)
    assert len(client.models.calls) == 1
    assert [item.source for item in judge.get_judgments()] == ["flash", "cache_hit"]


def test_surface_judge_flash_parse_failure_defaults_to_incompatible():
    judge = SurfaceJudge(flash_client=_FakeClient(responses=["not-json"]))

    result = judge.judge(
        _action("act_a", surface_context="glass table"),
        _action("act_b", surface_context="wood composite table"),
        "hard_effect",
    )

    assert result.result == "INCOMPATIBLE"
    assert result.source == "flash_error"
    assert judge.flash_call_count == 1
    assert len(judge.per_call_flash_latency_ms) == 1
    assert [item.code for item in judge.get_warnings()] == ["SURFACE_FLASH_PARSE_ERROR"]


def test_surface_judge_flash_api_failure_defaults_to_incompatible():
    judge = SurfaceJudge(flash_client=_FakeClient(exc=RuntimeError("network down")))

    result = judge.judge(
        _action("act_a", surface_context="glass table"),
        _action("act_b", surface_context="wood composite table"),
        "hard_effect",
    )

    assert result.result == "INCOMPATIBLE"
    assert result.source == "flash_error"
    assert judge.flash_call_count == 1
    assert len(judge.per_call_flash_latency_ms) == 1
    assert [item.code for item in judge.get_warnings()] == ["SURFACE_FLASH_ERROR"]
