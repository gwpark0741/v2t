from __future__ import annotations

import json
from pathlib import Path

import pytest

from v2t_prototype.artifacts import (
    FULL_VIDEO_ASSET_STAGE_DIR,
    LOCAL_PREPROCESSING_STAGE_DIR,
    RUN_MANIFEST_FILENAME,
    StageArtifactLoadError,
    get_stage_dir,
    load_stage_bundle,
    load_run_manifest,
    require_completed_stage_output,
    write_full_video_asset_artifacts,
    write_local_preprocessing_artifacts,
)
from v2t_prototype.models import (
    Cut,
    FullVideoAssetResult,
    LocalPreprocessingResult,
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
