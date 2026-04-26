from __future__ import annotations

import re
from typing import List

from .models import AgentARequest, AgentAResponse, Entity, Entity_Child, FullVideoAssetResult


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

    def validate_id(id_: str, *, context: str) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]*", id_) is None:
            issues.append(f"{context}: invalid snake_case ID '{id_}'")
        if id_ in seen_entity_ids:
            issues.append(f"{context}: duplicate ID '{id_}'")
        else:
            seen_entity_ids.add(id_)

    def validate_child(child: Entity_Child, *, parent_id: str) -> None:
        validate_id(child.id, context=f"Child of {parent_id}")

    def validate_entity(entity: Entity) -> None:
        validate_id(entity.id, context="Entity")
        for child in entity.children:
            validate_child(child, parent_id=entity.id)

    for entity in response.entity_registry.entities:
        validate_entity(entity)

    for ambience in response.entity_registry.ambience:
        validate_id(ambience.id, context="Ambience")

    for unknown in response.entity_registry.unknowns:
        if re.fullmatch(r"unknown_[0-9]+", unknown.id) is None:
            issues.append(f"Unknown: ID must match unknown_N pattern, got '{unknown.id}'")
        if unknown.id in seen_entity_ids:
            issues.append(f"Unknown: duplicate ID '{unknown.id}'")
        else:
            seen_entity_ids.add(unknown.id)

    return issues
