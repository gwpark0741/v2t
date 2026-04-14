from __future__ import annotations

from typing import Any

from .models import AgentBAllCutsResult, AgentCResult, EntityRegistry, PipelineResult, WarningItem
from .synthesizer import synthesize_tracks
from .surface_judge import DEFAULT_SURFACE_JUDGE_MODEL, SurfaceJudge
from .validation import validate_pipeline_result


def _build_source_entity_kind_by_id(entity_registry: EntityRegistry) -> dict[str, str]:
    source_entity_kind_by_id: dict[str, str] = {}
    for character in entity_registry.characters:
        source_entity_kind_by_id[character.id] = "Character"
    for key_object in entity_registry.key_objects:
        source_entity_kind_by_id[key_object.id] = "KeyObject"
    for ambience in entity_registry.ambience_sources:
        source_entity_kind_by_id[ambience.id] = "AmbienceSource"
    return source_entity_kind_by_id


def _warning_from_validation_issue(issue: str) -> WarningItem:
    if issue.startswith("duplicate track_id "):
        track_id = issue.removeprefix("duplicate track_id ").strip()
        return WarningItem(
            code="AGENT_C_DUPLICATE_TRACK_ID",
            severity="warning",
            message=f"Duplicate track_id detected in synthesized manifest: {track_id}",
            context={"issue": issue, "track_id": track_id},
        )
    return WarningItem(
        code="AGENT_C_PIPELINE_VALIDATION_WARNING",
        severity="warning",
        message="Pipeline validation reported a Stage 06 warning.",
        context={"issue": issue},
    )


def _empty_result_warning(pipeline_result: PipelineResult) -> WarningItem:
    return WarningItem(
        code="AGENT_C_EMPTY_RESULT",
        severity="warning",
        message="Agent C produced no tracks and no unresolved unknowns.",
        context={
            "track_count": len(pipeline_result.track_manifest.tracks),
            "unresolved_unknown_count": len(pipeline_result.unresolved_unknowns),
        },
    )


def run_agent_c(
    agent_b_all_cuts: AgentBAllCutsResult,
    entity_registry: EntityRegistry,
    *,
    flash_client: Any | None = None,
    flash_model: str = DEFAULT_SURFACE_JUDGE_MODEL,
) -> AgentCResult:
    actions = [
        action
        for cut_output in agent_b_all_cuts.cut_outputs
        for action in cut_output.actions
    ]
    surface_judge = SurfaceJudge(flash_client=flash_client, model=flash_model)
    pipeline_result = synthesize_tracks(
        actions,
        source_entity_kind_by_id=_build_source_entity_kind_by_id(entity_registry),
        surface_judge=surface_judge,
    )

    warnings = list(pipeline_result.warnings)
    warnings.extend(surface_judge.get_warnings())
    warnings.extend(
        _warning_from_validation_issue(issue) for issue in validate_pipeline_result(pipeline_result)
    )
    if not pipeline_result.track_manifest.tracks and not pipeline_result.unresolved_unknowns:
        warnings.append(_empty_result_warning(pipeline_result))

    pipeline_result = pipeline_result.model_copy(update={"warnings": warnings})
    return AgentCResult(
        pipeline_result=pipeline_result,
        surface_judgments=surface_judge.get_judgments(),
        merge_group_count=len(pipeline_result.track_manifest.tracks),
        flash_call_count=surface_judge.flash_call_count,
        cache_hit_count=surface_judge.cache_hit_count,
        total_flash_latency_ms=surface_judge.total_flash_latency_ms,
        per_call_flash_latency_ms=surface_judge.per_call_flash_latency_ms,
    )
