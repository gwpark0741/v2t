from __future__ import annotations

import re

from .entity_registry import get_all_valid_targets
from .models import AgentBCutInput, AgentBResponse, ContinuousEvent, Cut, EntityRegistry, OnsetEvent, SegmentClip


UNKNOWN_PATTERN = re.compile(r"^UNKNOWN_(CHARACTER|OBJECT|AMBIENCE)_CUT\d{3}_\d+$")
LOCAL_EVENT_TIME_TOLERANCE_SECONDS = 1e-3


def build_agent_b_cut_input(
    clip: SegmentClip,
    cut: Cut,
    entity_registry: EntityRegistry,
) -> AgentBCutInput:
    if clip.cut_id != cut.id:
        raise ValueError(f"clip.cut_id {clip.cut_id!r} != cut.id {cut.id!r}")

    return AgentBCutInput(
        cut_id=cut.id,
        cut_start_time=cut.start_time,
        cut_end_time=cut.end_time,
        clip_video_url=clip.clip_video_url,
        clip_video_mime_type=clip.clip_video_mime_type,
        entity_registry=entity_registry,
    )


def _registry_entity_ids(entity_registry: EntityRegistry) -> set[str]:
    return set(get_all_valid_targets(entity_registry))


def validate_agent_b_response(
    response: AgentBResponse,
    cut_id: str,
    entity_registry: EntityRegistry,
    *,
    cut_start_time: float,
    cut_end_time: float,
    tolerance_seconds: float = LOCAL_EVENT_TIME_TOLERANCE_SECONDS,
) -> list[str]:
    issues: list[str] = []
    all_registry_ids = _registry_entity_ids(entity_registry)
    seen_action_ids: set[str] = set()
    cut_duration = cut_end_time - cut_start_time

    for action in response.actions:
        if action.cut_id != cut_id:
            issues.append("AGENT_B_CUT_ID_MISMATCH")

        if action.action_id in seen_action_ids:
            issues.append("AGENT_B_DUPLICATE_ACTION_ID")
        else:
            seen_action_ids.add(action.action_id)

        if action.primary_source_id.startswith("UNKNOWN_"):
            if UNKNOWN_PATTERN.match(action.primary_source_id) is None:
                issues.append("AGENT_B_INVALID_UNKNOWN_FORMAT")
        else:
            if action.primary_source_id not in all_registry_ids:
                issues.append("AGENT_B_UNKNOWN_SOURCE_ID")

        if (
            action.unknown_resolution is not None
            and action.unknown_resolution.suggestion == "REASSIGN_TO_EXISTING"
            and action.unknown_resolution.suggested_entity_id not in all_registry_ids
        ):
            issues.append("AGENT_B_INVALID_REASSIGN_TARGET")

        event = action.event
        if isinstance(event, OnsetEvent):
            if event.timestamp > cut_duration + tolerance_seconds:
                issues.append("AGENT_B_EVENT_TIME_OUT_OF_LOCAL_RANGE")
        elif isinstance(event, ContinuousEvent):
            if (
                event.start_time > cut_duration + tolerance_seconds
                or event.end_time > cut_duration + tolerance_seconds
            ):
                issues.append("AGENT_B_EVENT_TIME_OUT_OF_LOCAL_RANGE")

    return issues
