from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Optional

from google import genai
from google.genai import types
from pydantic import ValidationError

from .agent_a_runtime import AgentARuntimeOutput
from .agent_b import build_agent_b_cut_input, validate_agent_b_response
from .entity_registry import derive_interaction_type, get_ambience_targets, get_sfx_targets
from .gemini_metrics import add_token_usage, estimate_model_cost_usd, extract_token_usage
from .gemini_client import create_gemini_client
from .models import (
    Action,
    AgentBAllCutsResult,
    AgentBCutInput,
    AgentBCutOutput,
    AgentBResponse,
    ContinuousEvent,
    EntityRegistry,
    OnsetEvent,
    SegmentPrepResult,
    TokenUsage,
    WarningItem,
    UnknownResolution,
)
from .prompts import load_prompt


DEFAULT_AGENT_B_MODEL = "gemini-2.5-pro"
DEFAULT_AGENT_B_SYSTEM_PROMPT = load_prompt("agent_b_system.md")
AGENT_B_USER_PROMPT_TEMPLATE = load_prompt("agent_b_user.md")

_EVENT_TYPE_NORMALIZATION = {
    "onset": "onset",
    "onsetevent": "onset",
    "continuous": "continuous",
    "continuousevent": "continuous",
}

_RETRYABLE_AGENT_B_ERROR_CODES = {429, 500, 502, 503, 504}
_AGENT_B_RETRY_BACKOFF_SECONDS = (2.0, 5.0, 10.0)


def build_agent_b_user_prompt(input: AgentBCutInput) -> str:
    sfx_targets_json = json.dumps(
        [
            {"id": target.id, "label": target.label}
            for target in get_sfx_targets(input.entity_registry).values()
        ],
        indent=2,
    )
    ambience_targets_json = json.dumps(
        [
            {"id": target.id, "label": target.label}
            for target in get_ambience_targets(input.entity_registry).values()
        ],
        indent=2,
    )
    unknowns_json = json.dumps(
        [
            {
                "id": unknown.id,
                "label": unknown.label,
                "visual_description": unknown.visual_description,
            }
            for unknown in input.entity_registry.unknowns
        ],
        indent=2,
    )
    return AGENT_B_USER_PROMPT_TEMPLATE.format(
        cut_id=input.cut_id,
        cut_start_time=input.cut_start_time,
        cut_end_time=input.cut_end_time,
        sfx_targets_json=sfx_targets_json,
        ambience_targets_json=ambience_targets_json,
        unknowns_json=unknowns_json,
    )


def _build_generation_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=DEFAULT_AGENT_B_SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_json_schema=AgentBResponse.model_json_schema(),
    )


def _normalize_event_type(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized_key = re.sub(r"[\s_]+", "", value.strip()).lower()
    return _EVENT_TYPE_NORMALIZATION.get(normalized_key, value)


def _normalize_agent_b_response_payload(raw_response_text: str) -> Any:
    payload = json.loads(raw_response_text)
    if not isinstance(payload, dict):
        return payload

    actions = payload.get("actions")
    if not isinstance(actions, list):
        return payload

    normalized_actions: list[Any] = []
    for action_payload in actions:
        if not isinstance(action_payload, dict):
            normalized_actions.append(action_payload)
            continue

        normalized_action = dict(action_payload)
        event_payload = normalized_action.get("event")
        if isinstance(event_payload, dict):
            normalized_event = dict(event_payload)
            normalized_event["type"] = _normalize_event_type(normalized_event.get("type"))
            normalized_action["event"] = normalized_event
        normalized_actions.append(normalized_action)

    normalized_payload = dict(payload)
    normalized_payload["actions"] = normalized_actions
    return normalized_payload


def _extract_error_status_code(exc: Exception) -> int | None:
    for attr_name in ("status_code", "code"):
        value = getattr(exc, attr_name, None)
        if isinstance(value, int):
            return value
        if callable(value):
            try:
                computed = value()
            except TypeError:
                continue
            if isinstance(computed, int):
                return computed

    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if isinstance(response_status, int):
        return response_status

    message = str(exc)
    match = re.search(r"\b(429|500|502|503|504)\b", message)
    if match:
        return int(match.group(1))
    return None


def _is_retryable_generation_error(exc: Exception) -> bool:
    status_code = _extract_error_status_code(exc)
    return status_code in _RETRYABLE_AGENT_B_ERROR_CODES


def _retry_backoff_seconds(retries_used: int) -> float:
    index = min(retries_used, len(_AGENT_B_RETRY_BACKOFF_SECONDS) - 1)
    return _AGENT_B_RETRY_BACKOFF_SECONDS[index]


def _normalize_action_event_to_absolute(action: Action, *, cut_start_time: float) -> Action:
    event = action.event
    if isinstance(event, OnsetEvent):
        normalized_event = event.model_copy(
            update={"timestamp": event.timestamp + cut_start_time}
        )
    else:
        normalized_event = event.model_copy(
            update={
                "start_time": event.start_time + cut_start_time,
                "end_time": event.end_time + cut_start_time,
            }
        )
    return action.model_copy(update={"event": normalized_event})


def _event_in_absolute_range(action: Action, *, cut_start_time: float, cut_end_time: float) -> bool:
    event = action.event
    if isinstance(event, OnsetEvent):
        return cut_start_time <= event.timestamp <= cut_end_time
    return (
        cut_start_time <= event.start_time < event.end_time <= cut_end_time
    )


def _postprocess_action(
    action: Action,
    *,
    cut_start_time: float,
    entity_registry: EntityRegistry,
) -> Action:
    primary_source_id = action.primary_source_id
    unknown_resolution: UnknownResolution | None = action.unknown_resolution
    if (
        unknown_resolution is not None
        and unknown_resolution.suggestion == "REASSIGN_TO_EXISTING"
        and unknown_resolution.suggested_entity_id
    ):
        primary_source_id = unknown_resolution.suggested_entity_id

    normalized_action = _normalize_action_event_to_absolute(
        action,
        cut_start_time=cut_start_time,
    )
    normalized_interaction_type = action.interaction_type
    derived_interaction_type = derive_interaction_type(primary_source_id, entity_registry)
    if derived_interaction_type is not None:
        normalized_interaction_type = derived_interaction_type
    return normalized_action.model_copy(
        update={
            "primary_source_id": primary_source_id,
            "interaction_type": normalized_interaction_type,
            "boundary_flag": False,
        }
    )


def run_agent_b_for_cut(
    input: AgentBCutInput,
    *,
    client: Optional[genai.Client] = None,
    model: str = DEFAULT_AGENT_B_MODEL,
    max_retries: int = 2,
) -> AgentBCutOutput:
    runtime_client = client or create_gemini_client()
    prompt = build_agent_b_user_prompt(input)
    clip_part = types.Part.from_uri(
        file_uri=input.clip_video_url,
        mime_type=input.clip_video_mime_type,
    )

    retries_used = 0
    aggregate_usage = TokenUsage()
    total_latency_ms = 0.0
    estimated_total_cost_usd = 0.0
    while True:
        started_at = time.monotonic()
        try:
            gemini_response = runtime_client.models.generate_content(
                model=model,
                contents=[clip_part, prompt],
                config=_build_generation_config(),
            )
        except Exception as exc:
            total_latency_ms += (time.monotonic() - started_at) * 1000.0
            if _is_retryable_generation_error(exc) and retries_used < max_retries:
                time.sleep(_retry_backoff_seconds(retries_used))
                retries_used += 1
                continue
            raise
        latency_ms = (time.monotonic() - started_at) * 1000.0
        usage = extract_token_usage(gemini_response)
        aggregate_usage = add_token_usage(aggregate_usage, usage)
        total_latency_ms += latency_ms
        estimated_total_cost_usd += estimate_model_cost_usd(model, usage)
        raw_response_text = gemini_response.text or ""

        try:
            normalized_payload = _normalize_agent_b_response_payload(raw_response_text)
            parsed_response = AgentBResponse.model_validate(normalized_payload)
        except json.JSONDecodeError:
            if retries_used < max_retries:
                retries_used += 1
                continue
            return AgentBCutOutput(
                cut_id=input.cut_id,
                raw_response_text=raw_response_text,
                actions=[],
                validation_issues=["AGENT_B_RESPONSE_PARSE_ERROR"],
                model=model,
                latency_ms=total_latency_ms,
                usage=aggregate_usage,
                estimated_cost_usd=estimated_total_cost_usd,
            )
        except ValidationError:
            if retries_used < max_retries:
                retries_used += 1
                continue
            return AgentBCutOutput(
                cut_id=input.cut_id,
                raw_response_text=raw_response_text,
                actions=[],
                validation_issues=["AGENT_B_RESPONSE_PARSE_ERROR"],
                model=model,
                latency_ms=total_latency_ms,
                usage=aggregate_usage,
                estimated_cost_usd=estimated_total_cost_usd,
            )

        issues = validate_agent_b_response(
            parsed_response,
            input.cut_id,
            input.entity_registry,
            cut_start_time=input.cut_start_time,
            cut_end_time=input.cut_end_time,
        )
        if issues and len(issues) <= 3 and retries_used < max_retries:
            retries_used += 1
            continue

        if issues and len(issues) > 3:
            return AgentBCutOutput(
                cut_id=input.cut_id,
                raw_response_text=raw_response_text,
                actions=[],
                validation_issues=issues,
                model=model,
                latency_ms=total_latency_ms,
                usage=aggregate_usage,
                estimated_cost_usd=estimated_total_cost_usd,
            )

        processed_actions = [
            _postprocess_action(
                action,
                cut_start_time=input.cut_start_time,
                entity_registry=input.entity_registry,
            )
            for action in parsed_response.actions
        ]
        absolute_issues = list(issues)
        for processed_action in processed_actions:
            if not _event_in_absolute_range(
                processed_action,
                cut_start_time=input.cut_start_time,
                cut_end_time=input.cut_end_time,
            ):
                absolute_issues.append("AGENT_B_EVENT_TIME_OUT_OF_ABSOLUTE_RANGE")

        return AgentBCutOutput(
            cut_id=input.cut_id,
            raw_response_text=raw_response_text,
            actions=processed_actions,
            validation_issues=absolute_issues,
            model=model,
            latency_ms=total_latency_ms,
            usage=aggregate_usage,
            estimated_cost_usd=estimated_total_cost_usd,
        )


async def run_agent_b_all_cuts_parallel(
    segment_prep: SegmentPrepResult,
    agent_a_output: AgentARuntimeOutput,
    *,
    client: Optional[genai.Client] = None,
    model: str = DEFAULT_AGENT_B_MODEL,
    max_concurrency: int = 5,
    max_retries: int = 2,
) -> AgentBAllCutsResult:
    runtime_client = client or create_gemini_client()
    cut_map = {cut.id: cut for cut in agent_a_output.request.cuts}
    skipped_cut_ids = [skipped.cut_id for skipped in segment_prep.skipped_cuts]
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _run_for_clip(clip) -> AgentBCutOutput:
        cut = cut_map.get(clip.cut_id)
        if cut is None:
            raise ValueError(f"Cut not found for clip: {clip.cut_id}")

        agent_b_input = build_agent_b_cut_input(
            clip=clip,
            cut=cut,
            entity_registry=agent_a_output.response.entity_registry,
        )

        async with semaphore:
            return await asyncio.to_thread(
                run_agent_b_for_cut,
                agent_b_input,
                client=runtime_client,
                model=model,
                max_retries=max_retries,
            )

    gathered = await asyncio.gather(
        *[_run_for_clip(clip) for clip in segment_prep.clips],
        return_exceptions=True,
    )

    cut_outputs_by_id: dict[str, AgentBCutOutput] = {}
    failed_clips: list[tuple[Any, Exception]] = []
    warnings: list[WarningItem] = []
    for clip, result in zip(segment_prep.clips, gathered):
        if isinstance(result, Exception):
            failed_clips.append((clip, result))
            continue
        cut_outputs_by_id[clip.cut_id] = result

    final_failed_cut_ids: list[str] = []
    for clip, initial_error in failed_clips:
        try:
            recovered_output = await _run_for_clip(clip)
        except Exception as recovery_error:
            final_failed_cut_ids.append(clip.cut_id)
            warnings.append(
                WarningItem(
                    code="AGENT_B_CUT_RUNTIME_FAILURE",
                    severity="warning",
                    message="Agent B runtime failed for one cut.",
                    context={
                        "cut_id": clip.cut_id,
                        "error": str(recovery_error),
                        "initial_error": str(initial_error),
                    },
                )
            )
            continue
        cut_outputs_by_id[clip.cut_id] = recovered_output

    cut_outputs = [
        cut_outputs_by_id[clip.cut_id]
        for clip in segment_prep.clips
        if clip.cut_id in cut_outputs_by_id
    ]

    total_actions = sum(len(output.actions) for output in cut_outputs)
    aggregate_usage = TokenUsage()
    total_model_latency_ms = 0.0
    estimated_total_cost_usd = 0.0
    for output in cut_outputs:
        aggregate_usage = add_token_usage(aggregate_usage, output.usage)
        total_model_latency_ms += output.latency_ms
        estimated_total_cost_usd += output.estimated_cost_usd
    unresolved_count = sum(
        1
        for output in cut_outputs
        for action in output.actions
        if action.primary_source_id.startswith("UNKNOWN_")
        and action.unknown_resolution is not None
        and action.unknown_resolution.suggestion == "UNRESOLVED"
    )
    reassigned_count = sum(
        1
        for output in cut_outputs
        for action in output.actions
        if action.unknown_resolution is not None
        and action.unknown_resolution.suggestion == "REASSIGN_TO_EXISTING"
    )

    return AgentBAllCutsResult(
        cut_outputs=cut_outputs,
        skipped_cut_ids=skipped_cut_ids,
        failed_cut_ids=final_failed_cut_ids,
        total_actions=total_actions,
        unresolved_count=unresolved_count,
        reassigned_count=reassigned_count,
        aggregate_usage=aggregate_usage,
        total_model_latency_ms=total_model_latency_ms,
        estimated_total_cost_usd=estimated_total_cost_usd,
        warnings=warnings,
    )
