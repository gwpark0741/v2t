from __future__ import annotations

from typing import List

from .models import AgentARequest, AgentAResponse, FullVideoAssetResult


def build_agent_a_request(full_video_asset: FullVideoAssetResult) -> AgentARequest:
    """
    Build the explicit Agent A request contract from Stage 02 output.
    """
    local = full_video_asset.local
    return AgentARequest(
        video_url=full_video_asset.video_url,
        video_mime_type=local.video_mime_type,
        video_metadata=local.video_metadata,
        cuts=local.cuts,
    )


def validate_agent_a_response(
    full_video_asset: FullVideoAssetResult, response: AgentAResponse
) -> List[str]:
    """
    Validate Agent A response entity registry invariants.
    """
    _ = full_video_asset
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
