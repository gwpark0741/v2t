from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal

from google.genai import types

from .gemini_client import (
    DEFAULT_TRACK_JUDGE_MODEL,
    GeminiGenerationParams,
    create_gemini_client,
    resolve_generation_params,
)
from .gemini_metrics import add_token_usage, estimate_model_cost_usd, extract_token_usage
from .models import Action, TokenUsage, TrackGroupJudgment, TrackGroupResult, WarningItem
from .prompts import load_prompt


TRACK_JUDGE_SYSTEM_PROMPT = load_prompt("track_judge_system.md")


@dataclass(frozen=True)
class _LLMGroupResult:
    groups: list[TrackGroupResult]
    source: Literal["llm", "llm_error"]


@dataclass(frozen=True)
class TrackJudgeDecision:
    groups: list[list[Action]]
    group_results: list[TrackGroupResult]
    source: Literal["single_action", "llm", "llm_error"]
    model: str | None


def _build_payload(
    actions: list[Action],
    source_id: str,
    interaction_type: str,
    event_type: str,
) -> str:
    return json.dumps(
        {
            "group_context": {
                "source_entity_id": source_id,
                "interaction_type": interaction_type,
                "event_type": event_type,
            },
            "actions": [
                {
                    "action_id": action.action_id,
                    "cut_id": action.cut_id,
                    "sound_description": action.sound_description,
                    "observed_visual_description": action.observed_visual_description,
                }
                for action in actions
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def _build_generation_config(
    *,
    temperature: float | None = None,
    generation_params: GeminiGenerationParams | None = None,
) -> types.GenerateContentConfig:
    params = resolve_generation_params(generation_params, temperature=temperature)
    return types.GenerateContentConfig(
        system_instruction=TRACK_JUDGE_SYSTEM_PROMPT,
        response_mime_type="application/json",
        **params.to_config_kwargs(),
    )


def _fallback_groups(actions: list[Action], *, reason: str = "fallback") -> list[TrackGroupResult]:
    return [
        TrackGroupResult(action_ids=[action.action_id], reason=reason)
        for action in actions
    ]


class TrackJudge:
    def __init__(
        self,
        flash_client: Any | None = None,
        model: str = DEFAULT_TRACK_JUDGE_MODEL,
        temperature: float | None = None,
        generation_params: GeminiGenerationParams | None = None,
    ) -> None:
        self._judgments: list[TrackGroupJudgment] = []
        self._warnings: list[WarningItem] = []
        self._llm_call_count = 0
        self._per_call_llm_latency_ms: list[float] = []
        self._llm_usage = TokenUsage()
        self._estimated_llm_cost_usd = 0.0
        self.flash_client = flash_client
        self.model = model
        self.temperature = temperature
        self.generation_params = generation_params

    @property
    def llm_call_count(self) -> int:
        return self._llm_call_count

    @property
    def total_llm_latency_ms(self) -> float:
        return sum(self._per_call_llm_latency_ms)

    @property
    def per_call_llm_latency_ms(self) -> list[float]:
        return list(self._per_call_llm_latency_ms)

    @property
    def llm_usage(self) -> TokenUsage:
        return self._llm_usage.model_copy()

    @property
    def estimated_llm_cost_usd(self) -> float:
        return self._estimated_llm_cost_usd

    def get_judgments(self) -> list[TrackGroupJudgment]:
        return list(self._judgments)

    def get_warnings(self) -> list[WarningItem]:
        return list(self._warnings)

    def judge_group(
        self,
        actions: list[Action],
        source_id: str,
        interaction_type: str,
        event_type: str,
    ) -> list[list[Action]]:
        decision = self.judge_group_decision(
            actions,
            source_id,
            interaction_type,
            event_type,
            record=True,
        )
        return decision.groups

    def judge_group_decision(
        self,
        actions: list[Action],
        source_id: str,
        interaction_type: str,
        event_type: str,
        *,
        record: bool,
    ) -> TrackJudgeDecision:
        group_key = f"{source_id}__{interaction_type}__{event_type}"
        if len(actions) == 1:
            group_results = [TrackGroupResult(action_ids=[actions[0].action_id], reason="single action")]
            if record:
                self.record_judgment(
                    group_key,
                    actions,
                    group_results,
                    source="single_action",
                    model=None,
                )
            return TrackJudgeDecision(
                groups=[list(actions)],
                group_results=group_results,
                source="single_action",
                model=None,
            )

        result = self._call_llm(actions, source_id, interaction_type, event_type)
        resolved_groups = self._resolve_groups(actions, result.groups)
        if record:
            self.record_judgment(
                group_key,
                actions,
                result.groups,
                source=result.source,
                model=self.model,
            )
        return TrackJudgeDecision(
            groups=resolved_groups,
            group_results=result.groups,
            source=result.source,
            model=self.model,
        )

    def _call_llm(
        self,
        actions: list[Action],
        source_id: str,
        interaction_type: str,
        event_type: str,
    ) -> _LLMGroupResult:
        self._llm_call_count += 1
        payload = _build_payload(actions, source_id, interaction_type, event_type)
        runtime_client = self.flash_client or create_gemini_client()
        started_at = time.monotonic()
        try:
            response = runtime_client.models.generate_content(
                model=self.model,
                contents=payload,
                config=_build_generation_config(
                    temperature=self.temperature,
                    generation_params=self.generation_params,
                ),
            )
        except Exception as exc:
            self._warnings.append(
                WarningItem(
                    code="TRACK_JUDGE_API_ERROR",
                    severity="warning",
                    message="TrackJudge LLM API call failed; falling back to per-action groups.",
                    context={
                        "source_entity_id": source_id,
                        "interaction_type": interaction_type,
                        "event_type": event_type,
                        "error": str(exc),
                    },
                )
            )
            return _LLMGroupResult(groups=_fallback_groups(actions), source="llm_error")

        latency_ms = (time.monotonic() - started_at) * 1000.0
        self._per_call_llm_latency_ms.append(latency_ms)
        usage = extract_token_usage(response)
        self._llm_usage = add_token_usage(self._llm_usage, usage)
        self._estimated_llm_cost_usd += estimate_model_cost_usd(self.model, usage)
        return self._parse_response(
            response.text or "",
            actions,
            source_id=source_id,
            interaction_type=interaction_type,
            event_type=event_type,
        )

    def _parse_response(
        self,
        raw: str,
        actions: list[Action],
        *,
        source_id: str,
        interaction_type: str,
        event_type: str,
    ) -> _LLMGroupResult:
        fallback = _fallback_groups(actions)
        try:
            data = json.loads(raw.strip())
            groups = data.get("groups")
            if not isinstance(groups, list) or not groups:
                raise ValueError("empty groups")

            parsed_groups: list[TrackGroupResult] = []
            for group in groups:
                if not isinstance(group, dict):
                    raise ValueError("invalid group item")
                action_ids = group.get("action_ids")
                reason = group.get("reason")
                if not isinstance(action_ids, list) or not action_ids:
                    raise ValueError("invalid action_ids")
                if not all(isinstance(action_id, str) for action_id in action_ids):
                    raise ValueError("non-string action_id")
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError("missing reason")
                parsed_groups.append(
                    TrackGroupResult(action_ids=action_ids, reason=reason)
                )

            input_ids = {action.action_id for action in actions}
            output_ids = [action_id for group in parsed_groups for action_id in group.action_ids]
            if len(output_ids) != len(set(output_ids)) or set(output_ids) != input_ids:
                self._warnings.append(
                    WarningItem(
                        code="TRACK_JUDGE_ID_MISMATCH",
                        severity="warning",
                        message="TrackJudge returned invalid action coverage; falling back to per-action groups.",
                        context={
                            "source_entity_id": source_id,
                            "interaction_type": interaction_type,
                            "event_type": event_type,
                            "input_action_ids": sorted(input_ids),
                            "output_action_ids": output_ids,
                        },
                    )
                )
                return _LLMGroupResult(groups=fallback, source="llm_error")

            return _LLMGroupResult(groups=parsed_groups, source="llm")
        except Exception as exc:
            self._warnings.append(
                WarningItem(
                    code="TRACK_JUDGE_PARSE_ERROR",
                    severity="warning",
                    message="TrackJudge response could not be parsed; falling back to per-action groups.",
                    context={
                        "source_entity_id": source_id,
                        "interaction_type": interaction_type,
                        "event_type": event_type,
                        "error": str(exc),
                        "raw_response_text": raw,
                    },
                )
            )
            return _LLMGroupResult(groups=fallback, source="llm_error")

    def _resolve_groups(
        self,
        actions: list[Action],
        groups: list[TrackGroupResult],
    ) -> list[list[Action]]:
        actions_by_id = {action.action_id: action for action in actions}
        return [
            [actions_by_id[action_id] for action_id in group.action_ids]
            for group in groups
        ]

    def record_judgment(
        self,
        group_key: str,
        actions: list[Action],
        groups: list[TrackGroupResult],
        *,
        source: Literal["single_action", "deterministic", "llm", "llm_error"],
        model: str | None,
    ) -> None:
        self._judgments.append(
            TrackGroupJudgment(
                group_key=group_key,
                input_action_ids=[action.action_id for action in actions],
                output_groups=groups,
                model=model,
                source=source,
            )
        )
