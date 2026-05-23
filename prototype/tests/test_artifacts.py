from __future__ import annotations

import json
from pathlib import Path

from v2t_prototype.agent_a_runtime import AgentARuntimeOutput
from v2t_prototype.artifacts import (
    AGENT_A_STAGE_DIR,
    AGENT_B_STAGE_DIR,
    AGENT_C_STAGE_DIR,
    LOCAL_PREPROCESSING_STAGE_DIR,
    RUN_MANIFEST_FILENAME,
    SEGMENT_PREP_STAGE_DIR,
    ensure_run_manifest,
    load_run_manifest,
    load_stage_bundle,
    require_completed_stage_output,
    write_agent_a_artifacts,
    write_agent_b_artifacts,
    write_agent_c_artifacts,
    write_local_preprocessing_artifacts,
    write_segment_prep_artifacts,
)
from v2t_prototype.models import (
    Action,
    AgentARequest,
    AgentAResponse,
    AgentBAllCutsResult,
    AgentBCutOutput,
    AgentCResult,
    Ambience,
    ContinuousEvent,
    Cut,
    Entity,
    Entity_Child,
    EntityRegistry,
    FullVideoAssetResult,
    LocalPreprocessingResult,
    PipelineResult,
    SegmentClip,
    SegmentPrepResult,
    StageStatus,
    Track,
    TrackManifest,
    Unknown,
    VideoMetadata,
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
            entities=[
                Entity(
                    id="fighter",
                    label="fighter",
                    children=[
                        Entity_Child(id="fighter_armor", label="fighter armor"),
                    ],
                )
            ],
            ambience=[Ambience(id="yard_wind", label="yard wind")],
            unknowns=[
                Unknown(
                    id="unknown_1",
                    label="unknown 1",
                    visual_description="wrapped item behind the fighter",
                )
            ],
        )
    )
    return AgentARuntimeOutput(
        request=request,
        response=response,
        raw_response_text=response.entity_registry.model_dump_json(),
    )


def _sample_agent_b_result() -> AgentBAllCutsResult:
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
                            "primary_source_id": "fighter_armor",
                            "interaction_type": "sfx",
                            "sound_description": "metal clash with a bright ring",
                            "observed_visual_description": "armor plates collide",
                            "event": ContinuousEvent(type="continuous", start_time=0.5, end_time=1.5),
                            "boundary_flag": False,
                        }
                    )
                ],
                validation_issues=[],
                model="gemini-2.5-pro",
            )
        ],
        skipped_cut_ids=[],
        failed_cut_ids=[],
        total_actions=1,
        unresolved_count=0,
        reassigned_count=0,
    )


def _sample_agent_c_result() -> AgentCResult:
    return AgentCResult(
        pipeline_result=PipelineResult(
            track_manifest=TrackManifest(
                tracks=[
                    Track(
                        track_number=1,
                        track_id="fighter_armor__sfx__continuous",
                        track_type="sfx",
                        source_entity_id="fighter_armor",
                        sound_description="metal clash with a bright ring",
                        events=[ContinuousEvent(type="continuous", start_time=0.5, end_time=1.5)],
                    )
                ]
            ),
            unresolved_unknowns=[],
            warnings=[],
        ),
        merge_group_count=1,
        llm_call_count=0,
        total_llm_latency_ms=0.0,
    )


def test_write_local_preprocessing_artifacts_writes_canonical_files(tmp_path: Path):
    result = _sample_local_result()

    stage_dir = write_local_preprocessing_artifacts(
        result,
        runs_dir=tmp_path,
        run_id="run_001",
        report_title="Stage 01 Report",
    )

    assert stage_dir == tmp_path / "run_001" / LOCAL_PREPROCESSING_STAGE_DIR
    assert (stage_dir / "output.json").exists()
    assert (stage_dir / "warnings.json").exists()
    assert (stage_dir / "report.html").exists()
    assert (tmp_path / "run_001" / RUN_MANIFEST_FILENAME).exists()

    output_payload = json.loads((stage_dir / "output.json").read_text(encoding="utf-8"))
    assert output_payload["video_path"] == "/tmp/sample_video.mp4"
    assert output_payload["cuts"][0]["id"] == "CUT_001"


def test_write_agent_a_artifacts_persists_hierarchical_registry(tmp_path: Path):
    local_stage_dir = write_local_preprocessing_artifacts(
        _sample_local_result(),
        runs_dir=tmp_path,
        run_id="run_001",
    )
    assert local_stage_dir.exists()
    stage_dir = write_agent_a_artifacts(
        _sample_agent_a_output(),
        full_video_asset=_sample_full_video_asset_result(),
        runs_dir=tmp_path,
        run_id="run_001",
    )

    output_payload = json.loads((stage_dir / "output.json").read_text(encoding="utf-8"))
    assert output_payload["response"]["entity_registry"]["entities"][0]["children"][0]["id"] == "fighter_armor"
    assert output_payload["response"]["entity_registry"]["unknowns"][0]["id"] == "unknown_1"
    assert (stage_dir / "input.json").exists()
    assert (stage_dir / "raw_response.txt").exists()
    assert (stage_dir / "report.html").exists()


def test_write_agent_b_and_agent_c_artifacts_roundtrip_with_new_schema(tmp_path: Path):
    write_local_preprocessing_artifacts(
        _sample_local_result(),
        runs_dir=tmp_path,
        run_id="run_001",
    )
    write_segment_prep_artifacts(
        _sample_segment_prep_result(),
        runs_dir=tmp_path,
        run_id="run_001",
        video_path="/tmp/sample_video.mp4",
    )
    agent_a_output = _sample_agent_a_output()
    write_agent_a_artifacts(
        agent_a_output,
        full_video_asset=_sample_full_video_asset_result(),
        runs_dir=tmp_path,
        run_id="run_001",
    )

    stage_b_dir = write_agent_b_artifacts(
        _sample_agent_b_result(),
        agent_a_output,
        video_path="/tmp/sample_video.mp4",
        runs_dir=tmp_path,
        run_id="run_001",
    )
    assert (stage_b_dir / "per_cut" / "CUT_001" / "input.json").exists()
    assert (stage_b_dir / "per_cut" / "CUT_001" / "output.json").exists()

    stage_c_dir = write_agent_c_artifacts(
        _sample_agent_c_result(),
        video_path="/tmp/sample_video.mp4",
        runs_dir=tmp_path,
        run_id="run_001",
    )
    bundle = load_stage_bundle(tmp_path / "run_001", AGENT_C_STAGE_DIR, AgentCResult)
    assert bundle.output is not None
    assert bundle.output.pipeline_result.track_manifest.tracks[0].source_entity_id == "fighter_armor"
    assert (stage_c_dir / "report.html").exists()


def test_ensure_run_manifest_and_require_completed_output_use_stage_statuses(tmp_path: Path):
    run_dir, manifest = ensure_run_manifest(
        runs_dir=tmp_path,
        run_id="run_001",
        video_path="/tmp/sample_video.mp4",
    )
    assert run_dir.exists()
    assert any(isinstance(stage, StageStatus) for stage in manifest.stages)

    write_local_preprocessing_artifacts(
        _sample_local_result(),
        runs_dir=tmp_path,
        run_id="run_001",
    )
    loaded = require_completed_stage_output(
        tmp_path / "run_001",
        LOCAL_PREPROCESSING_STAGE_DIR,
        LocalPreprocessingResult,
    )
    assert loaded.video_path == "/tmp/sample_video.mp4"
    assert load_run_manifest(tmp_path / "run_001").run_id == "run_001"
