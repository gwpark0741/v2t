from __future__ import annotations

from typing import List

from .models import AgentARequest, AgentAResponse, PreprocessingResult


def build_agent_a_request(preprocessing: PreprocessingResult) -> AgentARequest:
    """
    Build the explicit Agent A request contract from preprocessing output.
    """
    return AgentARequest(
        video_url=preprocessing.video_url,
        video_mime_type=preprocessing.video_mime_type,
        video_metadata=preprocessing.video_metadata,
        cuts=preprocessing.cuts,
    )


def validate_agent_a_response(
    preprocessing: PreprocessingResult, response: AgentAResponse
) -> List[str]:
    """
    Validate Agent A response entity registry invariants.
    """
    issues: List[str] = []

    seen_entity_ids: set[str] = set()
    for entity in response.entity_registry.characters:
        if not entity.id.startswith("char_"):
            issues.append(f"invalid character id prefix {entity.id}")
        if entity.id in seen_entity_ids:
            issues.append(f"duplicate entity id {entity.id}")
        seen_entity_ids.add(entity.id)

    for entity in response.entity_registry.key_objects:
        if not entity.id.startswith("obj_"):
            issues.append(f"invalid key object id prefix {entity.id}")
        if entity.id in seen_entity_ids:
            issues.append(f"duplicate entity id {entity.id}")
        seen_entity_ids.add(entity.id)

    for entity in response.entity_registry.ambience_sources:
        if not entity.id.startswith("amb_"):
            issues.append(f"invalid ambience source id prefix {entity.id}")
        if entity.id in seen_entity_ids:
            issues.append(f"duplicate entity id {entity.id}")
        seen_entity_ids.add(entity.id)

    return issues
