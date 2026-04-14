from __future__ import annotations

import re

from .models import AgentBCutInput, AgentBResponse, Cut, EntityRegistry, SegmentClip


UNKNOWN_PATTERN = re.compile(r"^UNKNOWN_(CHARACTER|OBJECT|AMBIENCE)_CUT\d{3}_\d+$")


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
    return (
        {item.id for item in entity_registry.characters}
        | {item.id for item in entity_registry.key_objects}
        | {item.id for item in entity_registry.ambience_sources}
    )


def validate_agent_b_response(
    response: AgentBResponse,
    cut_id: str,
    entity_registry: EntityRegistry,
) -> list[str]:
    issues: list[str] = []
    all_registry_ids = _registry_entity_ids(entity_registry)
    seen_action_ids: set[str] = set()

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
        elif action.primary_source_id not in all_registry_ids:
            issues.append("AGENT_B_UNKNOWN_SOURCE_ID")

        if (
            action.unknown_resolution is not None
            and action.unknown_resolution.suggestion == "REASSIGN_TO_EXISTING"
            and action.unknown_resolution.suggested_entity_id not in all_registry_ids
        ):
            issues.append("AGENT_B_INVALID_REASSIGN_TARGET")

    return issues
