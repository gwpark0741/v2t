from __future__ import annotations

from typing import Any

from .models import AgentBAllCutsResult, AgentCResult, EntityRegistry, PipelineResult, WarningItem
from .synthesizer import synthesize_tracks
from .track_judge import DEFAULT_TRACK_JUDGE_MODEL, TrackJudge
from .validation import validate_pipeline_result


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
    flash_model: str = DEFAULT_TRACK_JUDGE_MODEL,
) -> AgentCResult:
    _ = entity_registry
    actions = [
        action
        for cut_output in agent_b_all_cuts.cut_outputs
        for action in cut_output.actions
    ]
    track_judge = TrackJudge(flash_client=flash_client, model=flash_model)
    pipeline_result = synthesize_tracks(
        actions,
        track_judge=track_judge,
    )

    warnings = list(pipeline_result.warnings)
    warnings.extend(track_judge.get_warnings())
    warnings.extend(
        _warning_from_validation_issue(issue) for issue in validate_pipeline_result(pipeline_result)
    )
    if not pipeline_result.track_manifest.tracks and not pipeline_result.unresolved_unknowns:
        warnings.append(_empty_result_warning(pipeline_result))

    pipeline_result = pipeline_result.model_copy(update={"warnings": warnings})
    return AgentCResult(
        pipeline_result=pipeline_result,
        track_group_judgments=track_judge.get_judgments(),
        merge_group_count=len(pipeline_result.track_manifest.tracks),
        llm_call_count=track_judge.llm_call_count,
        total_llm_latency_ms=track_judge.total_llm_latency_ms,
        per_call_llm_latency_ms=track_judge.per_call_llm_latency_ms,
        llm_usage=track_judge.llm_usage,
        estimated_llm_cost_usd=track_judge.estimated_llm_cost_usd,
    )
