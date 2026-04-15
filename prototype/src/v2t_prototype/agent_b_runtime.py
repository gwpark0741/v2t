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
from .gemini_metrics import add_token_usage, estimate_model_cost_usd, extract_token_usage
from .gemini_client import create_gemini_client
from .models import (
    Action,
    AgentBAllCutsResult,
    AgentBCutInput,
    AgentBCutOutput,
    AgentBResponse,
    ContinuousEvent,
    OnsetEvent,
    SegmentPrepResult,
    TokenUsage,
    WarningItem,
    UnknownResolution,
)


DEFAULT_AGENT_B_MODEL = "gemini-2.5-pro"
DEFAULT_AGENT_B_SYSTEM_PROMPT = """You are Agent B in a video-to-sound metadata pipeline.
Your job is to analyze ONE silent video clip and identify all sound events
that would realistically occur in this clip.

You will receive:
  - A video clip (exact cut interval, no padding)
  - The authoritative cut interval: cut_id, start_time, end_time
  - An entity registry from Agent A (characters, key_objects, ambience_sources)

---

RULES

1. Identify ALL audible or likely-audible sound events in this clip.

2. For each sound event, assign primary_source_id:
   - Match to a registry entity: char_*, obj_*, amb_*
   - If no clear match: use UNKNOWN_{CHARACTER|OBJECT|AMBIENCE}_CUT{NNN}_{SEQ}

3. For UNKNOWN primary_source_id:
   - Review the ENTIRE registry again carefully before deciding.
   - If you find a matching entity with high confidence:
       suggestion: REASSIGN_TO_EXISTING
       suggested_entity_id: <registry id>
   - If uncertain or no match:
       suggestion: UNRESOLVED

4. Classify interaction_type — choose exactly one:
   sfx      — all individual sound events: physical contact, impact, friction,
              footsteps, clothing, body movement, mechanisms, electronic devices
   ambience — continuous spatial sound with no specific source:
              environment, crowd, wind, room tone

5. Fill these fields for every action:

   sound_description:
     - Describe the sound clearly in one sentence.
     - For sfx, include material/contact cues naturally if relevant.
     - Prefer descriptions that make the acoustic identity stable across cuts.

   Preferred style examples:
     Ambience:
       "Light, steady rain falling on wet city pavement and surfaces."
       "Gentle ocean waves washing ashore in the distance."
       "Arctic wind blowing during a heavy, steady snowfall."

     Sfx:
       "Slow, solitary footsteps with a slight splash on wet pavement, steady rhythm."
       "Forceful burst of powdery snow, a quick whoosh, and muffled landing."
       "deep wooden groans, hull straining, water splashing"

   observed_visual_description:
     Describe what you see that produces this sound.

6. Choose event type:
   - All event timestamps must be relative to THIS clip.
   - The clip always starts at 0.0 seconds.
   - Do NOT use full-video absolute timestamps.
   - onset:      single impact or instantaneous event
   - continuous: sustained sound with clear start and end

   Examples:
     {"event": {"type": "onset", "timestamp": 0.2}}
     {"event": {"type": "continuous", "start_time": 0.5, "end_time": 1.1}}

7. action_id format: act_{cut_id}_{SEQ:03d}
   Example: act_CUT001_001, act_CUT001_002

8. Constraints:
   - Do NOT add new entities to the registry.
   - Do NOT modify cut boundaries.
   - Set boundary_flag to false for all actions.
   - Return ONLY a JSON object matching the provided schema."""

_EVENT_TYPE_NORMALIZATION = {
    "onset": "onset",
    "onsetevent": "onset",
    "continuous": "continuous",
    "continuousevent": "continuous",
}


def _build_agent_b_prompt(input: AgentBCutInput) -> str:
    entity_registry_json = input.entity_registry.model_dump_json(indent=2)
    return (
        "Analyze the following cut segment.\n\n"
        f"cut_id: {input.cut_id}\n"
        f"Authoritative interval: {input.cut_start_time:.3f}s to {input.cut_end_time:.3f}s\n\n"
        "Entity registry:\n"
        f"{entity_registry_json}"
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


def _postprocess_action(action: Action, *, cut_start_time: float) -> Action:
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
    return normalized_action.model_copy(
        update={
            "primary_source_id": primary_source_id,
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
    prompt = _build_agent_b_prompt(input)
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
        gemini_response = runtime_client.models.generate_content(
            model=model,
            contents=[clip_part, prompt],
            config=_build_generation_config(),
        )
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
            _postprocess_action(action, cut_start_time=input.cut_start_time)
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

    cut_outputs: list[AgentBCutOutput] = []
    failed_cut_ids: list[str] = []
    warnings: list[WarningItem] = []
    for clip, result in zip(segment_prep.clips, gathered):
        if isinstance(result, Exception):
            failed_cut_ids.append(clip.cut_id)
            warnings.append(
                WarningItem(
                    code="AGENT_B_CUT_RUNTIME_FAILURE",
                    severity="warning",
                    message="Agent B runtime failed for one cut.",
                    context={"cut_id": clip.cut_id, "error": str(result)},
                )
            )
            continue
        cut_outputs.append(result)

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
        failed_cut_ids=failed_cut_ids,
        total_actions=total_actions,
        unresolved_count=unresolved_count,
        reassigned_count=reassigned_count,
        aggregate_usage=aggregate_usage,
        total_model_latency_ms=total_model_latency_ms,
        estimated_total_cost_usd=estimated_total_cost_usd,
        warnings=warnings,
    )
