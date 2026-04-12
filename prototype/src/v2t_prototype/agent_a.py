from __future__ import annotations

from typing import List

from .models import AgentARequest, AgentAResponse, PreprocessingResult


def build_agent_a_request(preprocessing: PreprocessingResult, video_uri: str) -> AgentARequest:
    """
    Build the explicit Agent A request contract from preprocessing output.
    """
    return AgentARequest(
        video_uri=video_uri,
        video_metadata=preprocessing.video_metadata,
        cuts=preprocessing.cuts,
    )


def validate_agent_a_response(
    preprocessing: PreprocessingResult, response: AgentAResponse
) -> List[str]:
    """
    Validate Agent A response linkage against authoritative preprocessing cuts.
    """
    issues: List[str] = []

    expected_cut_ids = {cut.id for cut in preprocessing.cuts}
    seen_cut_ids: set[str] = set()
    for enrichment in response.cut_enrichments:
        if enrichment.cut_id in seen_cut_ids:
            issues.append(f"duplicate cut enrichment {enrichment.cut_id}")
            continue
        seen_cut_ids.add(enrichment.cut_id)
        if enrichment.cut_id not in expected_cut_ids:
            issues.append(f"unknown cut_id in enrichment {enrichment.cut_id}")

    for missing_cut_id in sorted(expected_cut_ids - seen_cut_ids):
        issues.append(f"missing cut enrichment for {missing_cut_id}")

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
