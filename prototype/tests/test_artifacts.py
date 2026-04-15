from __future__ import annotations

import json
from pathlib import Path

import pytest

from v2t_prototype.agent_a_runtime import AgentARuntimeOutput
from v2t_prototype.artifacts import (
    AGENT_A_STAGE_DIR,
    AGENT_B_STAGE_DIR,
    AGENT_C_STAGE_DIR,
    FULL_VIDEO_ASSET_STAGE_DIR,
    LOCAL_PREPROCESSING_STAGE_DIR,
    RUN_MANIFEST_FILENAME,
    SEGMENT_PREP_STAGE_DIR,
    StageArtifactLoadError,
    ensure_run_manifest,
    get_stage_dir,
    load_stage_bundle,
    load_run_manifest,
    mark_stage_failed,
    require_completed_stage_output,
    write_stage_failure_artifacts,
    write_agent_a_artifacts,
    write_agent_b_artifacts,
    write_agent_c_artifacts,
    write_full_video_asset_artifacts,
    write_local_preprocessing_artifacts,
    write_segment_prep_artifacts,
)
from v2t_prototype.models import (
    Action,
    AgentARequest,
    AgentAResponse,
    AgentBAllCutsResult,
    AgentCResult,
    AgentBCutOutput,
    AmbienceSource,
    Character,
    Cut,
    ContinuousEvent,
    EntityRegistry,
    FullVideoAssetResult,
    Interval,
    KeyObject,
    LocalPreprocessingResult,
    PipelineResult,
    SegmentClip,
    SegmentPrepResult,
    SkippedCut,
    StageErrorRecord,
    Track,
    TrackGroupJudgment,
    TrackGroupResult,
    TrackManifest,
    TokenUsage,
    UnresolvedUnknown,
    UnknownResolution,
    VideoMetadata,
    WarningItem,
)


def _sample_local_result() -> LocalPreprocessingResult:
    return LocalPreprocessingResult(
        video_metadata=VideoMetadata(
            video_path="/tmp/sample_video.mp4",
            fps=24.0,
            frame_count=240,
            duration_seconds=10.0,
            width=1920,
            height=1080,
        ),
        cuts=[
            Cut(id="CUT_001", start_time=0.0, end_time=4.0),
            Cut(id="CUT_002", start_time=4.0, end_time=10.0),
        ],
        video_path="/tmp/sample_video.mp4",
        video_mime_type="video/mp4",
    )


def _sample_full_video_asset_result() -> FullVideoAssetResult:
    return FullVideoAssetResult(
        local=_sample_local_result(),
        video_url="https://generativelanguage.googleapis.com/v1beta/files/abc123",
        gemini_file_name="files/abc123",
        upload_timestamp_utc="2026-04-13T07:30:00Z",
    )


def _sample_segment_prep_result() -> SegmentPrepResult:
    return SegmentPrepResult(
        clips=[
            SegmentClip(
                cut_id="CUT_001",
                local_clip_path="/tmp/CUT_001.mp4",
                clip_video_url="https://generativelanguage.googleapis.com/v1beta/files/cut001",
                clip_gemini_file_name="files/cut001",
                clip_video_mime_type="video/mp4",
            )
        ],
        skipped_cuts=[
            SkippedCut(
                cut_id="CUT_002",
                reason="FFMPEG_FAILURE",
                error_detail="ffmpeg failed",
            )
        ],
        warnings=[
            WarningItem(
                code="SEGMENT_PREP_FFMPEG_FAILURE",
                severity="warning",
                message="Clip extraction failed for one cut.",
                context={"cut_id": "CUT_002"},
            )
        ],
    )


def _sample_agent_a_output() -> AgentARuntimeOutput:
    full_video_asset = _sample_full_video_asset_result()
    request = AgentARequest(
        video_url=full_video_asset.video_url,
        video_mime_type=full_video_asset.local.video_mime_type,
        video_metadata=full_video_asset.local.video_metadata,
        cuts=full_video_asset.local.cuts,
    )
    response = AgentAResponse(
        entity_registry=EntityRegistry(
            characters=[
                Character(
                    id="char_001",
                    label="Fighter",
                    visual_description="Armored fighter",
                    entry_exit_intervals=[Interval(start_time=0.0, end_time=10.0)],
                    audibility="likely_audible",
                )
            ],
            key_objects=[
                KeyObject(
                    id="obj_001",
                    label="Sword",
                    visual_description="Steel sword",
                    material="steel",
                    surface="polished",
                    has_mechanism=False,
                    audibility="audible",
                )
            ],
            ambience_sources=[
                AmbienceSource(
                    id="amb_001",
                    label="Yard",
                    space_description="Open yard",
                    distance_profile="mid",
                    tonal_quality="dry",
                )
            ],
        )
    )
    return AgentARuntimeOutput(
        request=request,
        response=response,
        raw_response_text=response.model_dump_json(),
    )


def _sample_agent_b_result(*, warnings: list[WarningItem] | None = None) -> AgentBAllCutsResult:
    return AgentBAllCutsResult(
        cut_outputs=[
            AgentBCutOutput(
                cut_id="CUT_001",
                raw_response_text='{"actions":[{"action_id":"raw_only_marker"}]}',
                actions=[
                    Action.model_validate(
                        {
                            "action_id": "act_CUT_001_001",
                            "cut_id": "CUT_001",
                            "primary_source_id": "UNKNOWN_OBJECT_CUT001_1",
                            "unknown_resolution": UnknownResolution(
                                suggestion="UNRESOLVED",
                                reason="unclear source",
                            ),
                            "interaction_type": "sfx",
                            "sound_description": "metal clash with a bright ring",
                            "observed_visual_description": "swords collide",
                            "event": ContinuousEvent(
                                type="continuous",
                                start_time=0.5,
                                end_time=1.5,
                            ),
                            "boundary_flag": False,
                        }
                    )
                ],
                validation_issues=["AGENT_B_UNKNOWN_SOURCE_ID"],
                model="gemini-2.5-pro",
            )
        ],
        skipped_cut_ids=["CUT_002"],
        failed_cut_ids=["CUT_003"],
        total_actions=1,
        unresolved_count=1,
        reassigned_count=0,
        warnings=list(warnings or []),
    )


def _sample_agent_c_result(
    *,
    warnings: list[WarningItem] | None = None,
    track_group_judgments: list[TrackGroupJudgment] | None = None,
    llm_call_count: int = 0,
    total_llm_latency_ms: float = 0.0,
    per_call_llm_latency_ms: list[float] | None = None,
) -> AgentCResult:
    return AgentCResult(
        pipeline_result=PipelineResult(
            track_manifest=TrackManifest(
                tracks=[
                    Track(
                        track_id="char_001__sfx__continuous",
                        track_type="sfx",
                        source_entity_id="char_001",
                        interaction_type="sfx",
                        sound_description="steel footstep",
                        events=[ContinuousEvent(type="continuous", start_time=0.5, end_time=1.5)],
                    )
                ]
            ),
            unresolved_unknowns=[
                UnresolvedUnknown(
                    unknown_id="UNKNOWN_OBJECT_CUT001_1",
                    cut_id="CUT_001",
                    observed_visual_description="swords collide",
                    interaction_type="sfx",
                    sound_description="metal clash with a bright ring",
                )
            ],
            warnings=list(warnings or []),
        ),
        track_group_judgments=list(track_group_judgments or []),
        merge_group_count=1,
        llm_call_count=llm_call_count,
        total_llm_latency_ms=total_llm_latency_ms,
        per_call_llm_latency_ms=list(per_call_llm_latency_ms or []),
        llm_usage=TokenUsage(),
        estimated_llm_cost_usd=0.0,
    )


def test_write_local_preprocessing_artifacts_writes_canonical_files(tmp_path: Path):
    result = _sample_local_result()
    warnings = [
        WarningItem(
            code="CUT_ENDS_BEFORE_VIDEO_DURATION",
            severity="warning",
            message="The last cut does not align with the full video duration.",
            context={"cut_id": "CUT_002"},
        )
    ]

    stage_dir = write_local_preprocessing_artifacts(
        result,
        runs_dir=tmp_path,
        run_id="run_001",
        warnings=warnings,
        report_title="Stage 01 Report",
    )

    assert stage_dir == tmp_path / "run_001" / LOCAL_PREPROCESSING_STAGE_DIR
    assert (stage_dir / "output.json").exists()
    assert (stage_dir / "warnings.json").exists()
    assert (stage_dir / "report.html").exists()

    output_payload = json.loads((stage_dir / "output.json").read_text(encoding="utf-8"))
    warnings_payload = json.loads((stage_dir / "warnings.json").read_text(encoding="utf-8"))

    assert output_payload["video_path"] == "/tmp/sample_video.mp4"
    assert output_payload["cuts"][0]["id"] == "CUT_001"
    assert warnings_payload["stage"] == LOCAL_PREPROCESSING_STAGE_DIR
    assert warnings_payload["warnings"][0]["code"] == "CUT_ENDS_BEFORE_VIDEO_DURATION"
    assert "Stage 01 Report" in (stage_dir / "report.html").read_text(encoding="utf-8")
    manifest = load_run_manifest(tmp_path / "run_001")
    stage_statuses = {stage.stage: stage for stage in manifest.stages}
    assert (tmp_path / "run_001" / RUN_MANIFEST_FILENAME).exists()
    assert manifest.run_id == "run_001"
    assert manifest.video_path == "/tmp/sample_video.mp4"
    assert stage_statuses[LOCAL_PREPROCESSING_STAGE_DIR].status == "completed"
    assert stage_statuses[FULL_VIDEO_ASSET_STAGE_DIR].status == "pending"


def test_write_full_video_asset_artifacts_writes_canonical_files(tmp_path: Path):
    result = _sample_full_video_asset_result()
    warnings = [
        WarningItem(
            code="UPLOAD_RETRIED_ONCE",
            severity="warning",
            message="Upload succeeded after one retry.",
            context={"attempt_count": 2},
        )
    ]

    stage_dir = write_full_video_asset_artifacts(
        result,
        runs_dir=tmp_path,
        run_id="run_001",
        warnings=warnings,
        report_title="Stage 02 Report",
    )

    assert stage_dir == tmp_path / "run_001" / FULL_VIDEO_ASSET_STAGE_DIR
    assert (stage_dir / "output.json").exists()
    assert (stage_dir / "warnings.json").exists()
    assert (stage_dir / "report.html").exists()

    output_payload = json.loads((stage_dir / "output.json").read_text(encoding="utf-8"))
    warnings_payload = json.loads((stage_dir / "warnings.json").read_text(encoding="utf-8"))

    assert output_payload["video_url"] == "https://generativelanguage.googleapis.com/v1beta/files/abc123"
    assert output_payload["gemini_file_name"] == "files/abc123"
    assert warnings_payload["stage"] == FULL_VIDEO_ASSET_STAGE_DIR
    assert warnings_payload["warnings"][0]["code"] == "UPLOAD_RETRIED_ONCE"
    assert "Stage 02 Report" in (stage_dir / "report.html").read_text(encoding="utf-8")


def test_write_agent_a_artifacts_writes_canonical_files_and_supports_reentry(tmp_path: Path):
    full_video_asset = _sample_full_video_asset_result()
    result = _sample_agent_a_output()
    warnings = [
        WarningItem(
            code="AGENT_A_RESPONSE_USED_FALLBACK_LABEL",
            severity="info",
            message="One label was normalized during reporting.",
            context={"entity_id": "char_001"},
        )
    ]

    stage_dir = write_agent_a_artifacts(
        result,
        full_video_asset=full_video_asset,
        runs_dir=tmp_path,
        run_id="run_agent_a_001",
        warnings=warnings,
        report_title="Stage 03 Report",
    )

    assert stage_dir == tmp_path / "run_agent_a_001" / AGENT_A_STAGE_DIR
    assert (stage_dir / "input.json").exists()
    assert (stage_dir / "output.json").exists()
    assert (stage_dir / "warnings.json").exists()
    assert (stage_dir / "raw_response.txt").exists()
    assert (stage_dir / "report.html").exists()

    input_payload = json.loads((stage_dir / "input.json").read_text(encoding="utf-8"))
    output_payload = json.loads((stage_dir / "output.json").read_text(encoding="utf-8"))
    warnings_payload = json.loads((stage_dir / "warnings.json").read_text(encoding="utf-8"))

    assert input_payload["video_url"] == full_video_asset.video_url
    assert output_payload["request"]["cuts"][0]["id"] == "CUT_001"
    assert output_payload["response"]["entity_registry"]["characters"][0]["id"] == "char_001"
    assert warnings_payload["stage"] == AGENT_A_STAGE_DIR
    assert warnings_payload["warnings"][0]["code"] == "AGENT_A_RESPONSE_USED_FALLBACK_LABEL"
    assert "Stage 03 Report" in (stage_dir / "report.html").read_text(encoding="utf-8")
    assert (stage_dir / "raw_response.txt").read_text(encoding="utf-8") == result.raw_response_text

    manifest = load_run_manifest(tmp_path / "run_agent_a_001")
    stage_statuses = {stage.stage: stage for stage in manifest.stages}
    assert stage_statuses[AGENT_A_STAGE_DIR].status == "completed"

    bundle = load_stage_bundle(
        tmp_path / "run_agent_a_001",
        AGENT_A_STAGE_DIR,
        AgentARuntimeOutput,
    )
    assert bundle.status == "completed"
    assert bundle.output == result
    assert bundle.output_path == stage_dir / "output.json"
    assert bundle.warnings_path == stage_dir / "warnings.json"
    assert bundle.report_path == stage_dir / "report.html"

    reentry_output = require_completed_stage_output(
        tmp_path / "run_agent_a_001",
        AGENT_A_STAGE_DIR,
        AgentARuntimeOutput,
    )
    assert reentry_output == result


def test_write_agent_a_artifacts_writes_warnings_file_even_when_empty(tmp_path: Path):
    stage_dir = write_agent_a_artifacts(
        _sample_agent_a_output(),
        full_video_asset=_sample_full_video_asset_result(),
        runs_dir=tmp_path,
        run_id="run_agent_a_002",
        warnings=[],
    )

    warnings_payload = json.loads((stage_dir / "warnings.json").read_text(encoding="utf-8"))
    assert warnings_payload["stage"] == AGENT_A_STAGE_DIR
    assert warnings_payload["warnings"] == []


def test_write_segment_prep_artifacts_writes_canonical_files(tmp_path: Path):
    result = _sample_segment_prep_result()

    stage_dir = write_segment_prep_artifacts(
        result,
        runs_dir=tmp_path,
        run_id="run_segment_001",
        video_path="/tmp/sample_video.mp4",
        report_title="Stage 04 Report",
    )

    assert stage_dir == tmp_path / "run_segment_001" / SEGMENT_PREP_STAGE_DIR
    assert (stage_dir / "output.json").exists()
    assert (stage_dir / "warnings.json").exists()
    assert (stage_dir / "report.html").exists()

    output_payload = json.loads((stage_dir / "output.json").read_text(encoding="utf-8"))
    warnings_payload = json.loads((stage_dir / "warnings.json").read_text(encoding="utf-8"))

    assert output_payload["clips"][0]["cut_id"] == "CUT_001"
    assert output_payload["skipped_cuts"][0]["cut_id"] == "CUT_002"
    assert warnings_payload["stage"] == SEGMENT_PREP_STAGE_DIR
    assert warnings_payload["warnings"][0]["code"] == "SEGMENT_PREP_FFMPEG_FAILURE"
    assert "Stage 04 Report" in (stage_dir / "report.html").read_text(encoding="utf-8")

    manifest = load_run_manifest(tmp_path / "run_segment_001")
    stage_statuses = {stage.stage: stage for stage in manifest.stages}
    assert stage_statuses[SEGMENT_PREP_STAGE_DIR].status == "completed"


def test_write_agent_b_artifacts_writes_canonical_files_and_supports_reentry(tmp_path: Path):
    run_id = "run_agent_b_001"
    write_segment_prep_artifacts(
        _sample_segment_prep_result(),
        runs_dir=tmp_path,
        run_id=run_id,
        video_path="/tmp/sample_video.mp4",
    )
    result = _sample_agent_b_result(
        warnings=[
            WarningItem(
                code="AGENT_B_CUT_RUNTIME_FAILURE",
                severity="warning",
                message="runtime failed",
                context={"cut_id": "CUT_003"},
            )
        ]
    )

    stage_dir = write_agent_b_artifacts(
        result,
        _sample_agent_a_output(),
        video_path="/tmp/sample_video.mp4",
        runs_dir=tmp_path,
        run_id=run_id,
        report_title="Stage 05 Report",
    )

    assert stage_dir == tmp_path / run_id / AGENT_B_STAGE_DIR
    assert (stage_dir / "output.json").exists()
    assert (stage_dir / "warnings.json").exists()
    assert (stage_dir / "report.html").exists()
    assert (stage_dir / "per_cut" / "CUT_001" / "input.json").exists()
    assert (stage_dir / "per_cut" / "CUT_001" / "output.json").exists()
    assert (stage_dir / "per_cut" / "CUT_001" / "raw_response.txt").exists()

    input_payload = json.loads(
        (stage_dir / "per_cut" / "CUT_001" / "input.json").read_text(encoding="utf-8")
    )
    output_payload = json.loads((stage_dir / "output.json").read_text(encoding="utf-8"))
    warnings_payload = json.loads((stage_dir / "warnings.json").read_text(encoding="utf-8"))

    assert input_payload["cut_id"] == "CUT_001"
    assert input_payload["clip_video_url"].endswith("cut001")
    assert output_payload["cut_outputs"][0]["cut_id"] == "CUT_001"
    assert warnings_payload["stage"] == AGENT_B_STAGE_DIR
    assert warnings_payload["warnings"][0]["code"] == "AGENT_B_CUT_RUNTIME_FAILURE"
    assert "Stage 05 Report" in (stage_dir / "report.html").read_text(encoding="utf-8")

    manifest = load_run_manifest(tmp_path / run_id)
    stage_statuses = {stage.stage: stage for stage in manifest.stages}
    assert stage_statuses[AGENT_B_STAGE_DIR].status == "completed"

    bundle = load_stage_bundle(
        tmp_path / run_id,
        AGENT_B_STAGE_DIR,
        AgentBAllCutsResult,
    )
    assert bundle.status == "completed"
    assert bundle.output == result
    assert bundle.output_path == stage_dir / "output.json"
    assert bundle.warnings_path == stage_dir / "warnings.json"
    assert bundle.report_path == stage_dir / "report.html"

    reentry_output = require_completed_stage_output(
        tmp_path / run_id,
        AGENT_B_STAGE_DIR,
        AgentBAllCutsResult,
    )
    assert reentry_output == result


def test_write_agent_b_artifacts_writes_warnings_file_even_when_empty(tmp_path: Path):
    run_id = "run_agent_b_002"
    write_segment_prep_artifacts(
        _sample_segment_prep_result(),
        runs_dir=tmp_path,
        run_id=run_id,
        video_path="/tmp/sample_video.mp4",
    )

    stage_dir = write_agent_b_artifacts(
        _sample_agent_b_result(warnings=[]),
        _sample_agent_a_output(),
        video_path="/tmp/sample_video.mp4",
        runs_dir=tmp_path,
        run_id=run_id,
    )

    warnings_payload = json.loads((stage_dir / "warnings.json").read_text(encoding="utf-8"))
    assert warnings_payload["stage"] == AGENT_B_STAGE_DIR
    assert warnings_payload["warnings"] == []


def test_write_agent_c_artifacts_writes_canonical_files_and_supports_reentry(tmp_path: Path):
    result = _sample_agent_c_result(
        warnings=[
            WarningItem(
                code="AGENT_C_PIPELINE_VALIDATION_WARNING",
                severity="warning",
                message="Pipeline validation reported a Stage 06 warning.",
                context={"issue": "misc warning"},
            )
        ]
    )

    stage_dir = write_agent_c_artifacts(
        result,
        video_path="/tmp/sample_video.mp4",
        runs_dir=tmp_path,
        run_id="run_agent_c_001",
        report_title="Stage 06 Report",
    )

    assert stage_dir == tmp_path / "run_agent_c_001" / AGENT_C_STAGE_DIR
    assert (stage_dir / "output.json").exists()
    assert (stage_dir / "warnings.json").exists()
    assert (stage_dir / "track_group_judgments.json").exists()
    assert (stage_dir / "report.html").exists()

    output_payload = json.loads((stage_dir / "output.json").read_text(encoding="utf-8"))
    warnings_payload = json.loads((stage_dir / "warnings.json").read_text(encoding="utf-8"))
    track_group_payload = json.loads((stage_dir / "track_group_judgments.json").read_text(encoding="utf-8"))

    assert output_payload["merge_group_count"] == 1
    assert output_payload["pipeline_result"]["track_manifest"]["tracks"][0]["track_id"] == "char_001__sfx__continuous"
    assert warnings_payload["stage"] == AGENT_C_STAGE_DIR
    assert warnings_payload["warnings"][0]["code"] == "AGENT_C_PIPELINE_VALIDATION_WARNING"
    assert track_group_payload == {"track_group_judgments": []}
    assert "Stage 06 Report" in (stage_dir / "report.html").read_text(encoding="utf-8")

    manifest = load_run_manifest(tmp_path / "run_agent_c_001")
    stage_statuses = {stage.stage: stage for stage in manifest.stages}
    assert stage_statuses[AGENT_C_STAGE_DIR].status == "completed"

    bundle = load_stage_bundle(
        tmp_path / "run_agent_c_001",
        AGENT_C_STAGE_DIR,
        AgentCResult,
    )
    assert bundle.status == "completed"
    assert bundle.output == result
    assert bundle.output_path == stage_dir / "output.json"
    assert bundle.warnings_path == stage_dir / "warnings.json"
    assert bundle.report_path == stage_dir / "report.html"

    reentry_output = require_completed_stage_output(
        tmp_path / "run_agent_c_001",
        AGENT_C_STAGE_DIR,
        AgentCResult,
    )
    assert reentry_output == result


def test_write_agent_c_artifacts_writes_warnings_file_even_when_empty(tmp_path: Path):
    stage_dir = write_agent_c_artifacts(
        _sample_agent_c_result(warnings=[]),
        video_path="/tmp/sample_video.mp4",
        runs_dir=tmp_path,
        run_id="run_agent_c_002",
    )

    warnings_payload = json.loads((stage_dir / "warnings.json").read_text(encoding="utf-8"))
    track_group_payload = json.loads((stage_dir / "track_group_judgments.json").read_text(encoding="utf-8"))
    assert warnings_payload["stage"] == AGENT_C_STAGE_DIR
    assert warnings_payload["warnings"] == []
    assert track_group_payload == {"track_group_judgments": []}


def test_write_agent_c_artifacts_persists_track_group_judgments(tmp_path: Path):
    stage_dir = write_agent_c_artifacts(
        _sample_agent_c_result(
            track_group_judgments=[
                TrackGroupJudgment(
                    group_key="obj_001__sfx__onset",
                    input_action_ids=["act_001", "act_002"],
                    output_groups=[
                        TrackGroupResult(
                            action_ids=["act_001"],
                            reason="separate click",
                        ),
                        TrackGroupResult(
                            action_ids=["act_002"],
                            reason="separate thump",
                        ),
                    ],
                    source="llm",
                    model="gemini-2.5-flash",
                )
            ],
            llm_call_count=1,
            total_llm_latency_ms=18.75,
            per_call_llm_latency_ms=[12.5, 6.25],
        ),
        video_path="/tmp/sample_video.mp4",
        runs_dir=tmp_path,
        run_id="run_agent_c_003",
    )

    track_group_payload = json.loads((stage_dir / "track_group_judgments.json").read_text(encoding="utf-8"))
    assert track_group_payload["track_group_judgments"][0]["source"] == "llm"
    assert track_group_payload["track_group_judgments"][0]["model"] == "gemini-2.5-flash"
    output_payload = json.loads((stage_dir / "output.json").read_text(encoding="utf-8"))
    assert output_payload["llm_call_count"] == 1
    assert output_payload["total_llm_latency_ms"] == 18.75
    assert output_payload["per_call_llm_latency_ms"] == [12.5, 6.25]


def test_manifest_tracks_stage_progress_across_stage_01_and_02(tmp_path: Path):
    local_result = _sample_local_result()
    asset_result = _sample_full_video_asset_result()

    write_local_preprocessing_artifacts(
        local_result,
        runs_dir=tmp_path,
        run_id="run_002",
        warnings=[],
    )
    write_full_video_asset_artifacts(
        asset_result,
        runs_dir=tmp_path,
        run_id="run_002",
        warnings=[],
    )

    manifest = load_run_manifest(tmp_path / "run_002")
    stage_statuses = {stage.stage: stage for stage in manifest.stages}

    assert stage_statuses[LOCAL_PREPROCESSING_STAGE_DIR].status == "completed"
    assert stage_statuses[FULL_VIDEO_ASSET_STAGE_DIR].status == "completed"
    assert stage_statuses[LOCAL_PREPROCESSING_STAGE_DIR].completed_at is not None
    assert stage_statuses[FULL_VIDEO_ASSET_STAGE_DIR].completed_at is not None


def test_load_stage_bundle_returns_completed_stage_01_output_and_warnings(tmp_path: Path):
    local_result = _sample_local_result()
    warnings = [
        WarningItem(
            code="CUT_ENDS_BEFORE_VIDEO_DURATION",
            severity="warning",
            message="The last cut does not align with the full video duration.",
            context={"cut_id": "CUT_002"},
        )
    ]
    write_local_preprocessing_artifacts(
        local_result,
        runs_dir=tmp_path,
        run_id="run_003",
        warnings=warnings,
    )

    bundle = load_stage_bundle(
        tmp_path / "run_003",
        LOCAL_PREPROCESSING_STAGE_DIR,
        LocalPreprocessingResult,
    )

    assert bundle.status == "completed"
    assert bundle.output == local_result
    assert bundle.output_path == get_stage_dir(tmp_path / "run_003", LOCAL_PREPROCESSING_STAGE_DIR) / "output.json"
    assert bundle.warnings_path == get_stage_dir(tmp_path / "run_003", LOCAL_PREPROCESSING_STAGE_DIR) / "warnings.json"
    assert bundle.error is None
    assert [item.code for item in bundle.warnings] == ["CUT_ENDS_BEFORE_VIDEO_DURATION"]


def test_load_stage_bundle_returns_pending_stage_without_output(tmp_path: Path):
    write_local_preprocessing_artifacts(
        _sample_local_result(),
        runs_dir=tmp_path,
        run_id="run_004",
        warnings=[],
    )

    bundle = load_stage_bundle(tmp_path / "run_004", FULL_VIDEO_ASSET_STAGE_DIR)

    assert bundle.status == "pending"
    assert bundle.output is None
    assert bundle.warnings == []
    assert bundle.error is None


def test_require_completed_stage_output_raises_for_pending_stage(tmp_path: Path):
    write_local_preprocessing_artifacts(
        _sample_local_result(),
        runs_dir=tmp_path,
        run_id="run_005",
        warnings=[],
    )

    with pytest.raises(StageArtifactLoadError):
        require_completed_stage_output(
            tmp_path / "run_005",
            FULL_VIDEO_ASSET_STAGE_DIR,
            FullVideoAssetResult,
        )


def test_load_stage_bundle_raises_on_output_schema_mismatch(tmp_path: Path):
    write_local_preprocessing_artifacts(
        _sample_local_result(),
        runs_dir=tmp_path,
        run_id="run_006",
        warnings=[],
    )
    output_path = tmp_path / "run_006" / LOCAL_PREPROCESSING_STAGE_DIR / "output.json"
    output_path.write_text('{"not":"the expected schema"}', encoding="utf-8")

    with pytest.raises(StageArtifactLoadError):
        load_stage_bundle(
            tmp_path / "run_006",
            LOCAL_PREPROCESSING_STAGE_DIR,
            LocalPreprocessingResult,
        )


def test_load_stage_bundle_rejects_legacy_padded_stage_04_output(tmp_path: Path):
    result = _sample_segment_prep_result()
    write_segment_prep_artifacts(
        result,
        runs_dir=tmp_path,
        run_id="run_legacy_stage_04",
        video_path="/tmp/sample_video.mp4",
    )
    output_path = tmp_path / "run_legacy_stage_04" / SEGMENT_PREP_STAGE_DIR / "output.json"
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    payload["clips"][0]["padded_start_time"] = 0.0
    payload["clips"][0]["padded_end_time"] = 4.0
    payload["clips"][0]["actual_padding_start"] = 0.0
    payload["clips"][0]["actual_padding_end"] = 0.0
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    with pytest.raises(StageArtifactLoadError):
        load_stage_bundle(
            tmp_path / "run_legacy_stage_04",
            SEGMENT_PREP_STAGE_DIR,
            SegmentPrepResult,
        )


def test_write_stage_failure_artifacts_records_error_and_failed_status(tmp_path: Path):
    error = StageErrorRecord(
        stage=FULL_VIDEO_ASSET_STAGE_DIR,
        failed_at_utc="2026-04-13T08:00:00Z",
        attempt_count=3,
        retryable=True,
        error_class="GeminiFileUploadTimeoutError",
        message="Uploaded file did not become ACTIVE within timeout.",
        context={"timeout_seconds": 60.0},
    )
    warnings = [
        WarningItem(
            code="UPLOAD_RETRY_EXHAUSTED",
            severity="error",
            message="Upload retries were exhausted before the file became ACTIVE.",
            context={"attempt_count": 3},
        )
    ]

    stage_dir = write_stage_failure_artifacts(
        stage=FULL_VIDEO_ASSET_STAGE_DIR,
        video_path="/tmp/sample_video.mp4",
        error=error,
        runs_dir=tmp_path,
        run_id="run_007",
        warnings=warnings,
    )

    assert stage_dir == tmp_path / "run_007" / FULL_VIDEO_ASSET_STAGE_DIR
    assert (stage_dir / "warnings.json").exists()
    assert (stage_dir / "error.json").exists()
    assert not (stage_dir / "output.json").exists()

    manifest = load_run_manifest(tmp_path / "run_007")
    stage_statuses = {stage.stage: stage for stage in manifest.stages}
    assert stage_statuses[FULL_VIDEO_ASSET_STAGE_DIR].status == "failed"
    assert stage_statuses[FULL_VIDEO_ASSET_STAGE_DIR].error_message == error.message

    bundle = load_stage_bundle(tmp_path / "run_007", FULL_VIDEO_ASSET_STAGE_DIR)
    assert bundle.status == "failed"
    assert bundle.output is None
    assert bundle.error == error
    assert [item.code for item in bundle.warnings] == ["UPLOAD_RETRY_EXHAUSTED"]


def test_write_stage_failure_artifacts_records_stage_03_error_and_failed_status(tmp_path: Path):
    error = StageErrorRecord(
        stage=AGENT_A_STAGE_DIR,
        failed_at_utc="2026-04-14T08:00:00Z",
        attempt_count=2,
        retryable=False,
        error_class="AgentAArtifactWriteError",
        message="Stage 03 artifacts could not be written.",
        context={"run_id": "run_010"},
    )

    stage_dir = write_stage_failure_artifacts(
        stage=AGENT_A_STAGE_DIR,
        video_path="/tmp/sample_video.mp4",
        error=error,
        runs_dir=tmp_path,
        run_id="run_010",
        warnings=[],
    )

    assert stage_dir == tmp_path / "run_010" / AGENT_A_STAGE_DIR
    assert (stage_dir / "warnings.json").exists()
    assert (stage_dir / "error.json").exists()
    assert not (stage_dir / "output.json").exists()

    manifest = load_run_manifest(tmp_path / "run_010")
    stage_statuses = {stage.stage: stage for stage in manifest.stages}
    assert stage_statuses[AGENT_A_STAGE_DIR].status == "failed"
    assert stage_statuses[AGENT_A_STAGE_DIR].error_message == error.message

    bundle = load_stage_bundle(tmp_path / "run_010", AGENT_A_STAGE_DIR)
    assert bundle.status == "failed"
    assert bundle.output is None
    assert bundle.error == error


def test_write_stage_failure_artifacts_records_stage_05_error_and_failed_status(tmp_path: Path):
    error = StageErrorRecord(
        stage=AGENT_B_STAGE_DIR,
        failed_at_utc="2026-04-14T08:00:00Z",
        attempt_count=2,
        retryable=False,
        error_class="AgentBArtifactWriteError",
        message="Stage 05 artifacts could not be written.",
        context={"cut_id": "CUT_001"},
    )

    stage_dir = write_stage_failure_artifacts(
        stage=AGENT_B_STAGE_DIR,
        video_path="/tmp/sample_video.mp4",
        error=error,
        runs_dir=tmp_path,
        run_id="run_009",
        warnings=[],
    )

    assert stage_dir == tmp_path / "run_009" / AGENT_B_STAGE_DIR
    assert (stage_dir / "warnings.json").exists()
    assert (stage_dir / "error.json").exists()
    assert not (stage_dir / "output.json").exists()

    manifest = load_run_manifest(tmp_path / "run_009")
    stage_statuses = {stage.stage: stage for stage in manifest.stages}
    assert stage_statuses[AGENT_B_STAGE_DIR].status == "failed"
    assert stage_statuses[AGENT_B_STAGE_DIR].error_message == error.message

    bundle = load_stage_bundle(tmp_path / "run_009", AGENT_B_STAGE_DIR)
    assert bundle.status == "failed"
    assert bundle.output is None
    assert bundle.error == error


def test_write_stage_failure_artifacts_records_stage_06_error_and_failed_status(tmp_path: Path):
    error = StageErrorRecord(
        stage=AGENT_C_STAGE_DIR,
        failed_at_utc="2026-04-14T08:00:00Z",
        attempt_count=2,
        retryable=False,
        error_class="AgentCArtifactWriteError",
        message="Stage 06 artifacts could not be written.",
        context={"run_id": "run_011"},
    )

    stage_dir = write_stage_failure_artifacts(
        stage=AGENT_C_STAGE_DIR,
        video_path="/tmp/sample_video.mp4",
        error=error,
        runs_dir=tmp_path,
        run_id="run_011",
        warnings=[],
    )

    assert stage_dir == tmp_path / "run_011" / AGENT_C_STAGE_DIR
    assert (stage_dir / "warnings.json").exists()
    assert (stage_dir / "error.json").exists()
    assert not (stage_dir / "output.json").exists()

    manifest = load_run_manifest(tmp_path / "run_011")
    stage_statuses = {stage.stage: stage for stage in manifest.stages}
    assert stage_statuses[AGENT_C_STAGE_DIR].status == "failed"
    assert stage_statuses[AGENT_C_STAGE_DIR].error_message == error.message

    bundle = load_stage_bundle(tmp_path / "run_011", AGENT_C_STAGE_DIR)
    assert bundle.status == "failed"
    assert bundle.output is None
    assert bundle.error == error


def test_mark_stage_failed_updates_manifest_status_and_error_message(tmp_path: Path):
    _, manifest = ensure_run_manifest(
        runs_dir=tmp_path,
        run_id="run_008",
        video_path="/tmp/sample_video.mp4",
    )

    updated = mark_stage_failed(
        manifest,
        stage_dir=FULL_VIDEO_ASSET_STAGE_DIR,
        error_message="Upload failed after retries.",
    )
    stage_statuses = {stage.stage: stage for stage in updated.stages}

    assert stage_statuses[FULL_VIDEO_ASSET_STAGE_DIR].status == "failed"
    assert stage_statuses[FULL_VIDEO_ASSET_STAGE_DIR].error_message == "Upload failed after retries."
    assert stage_statuses[FULL_VIDEO_ASSET_STAGE_DIR].completed_at is not None
