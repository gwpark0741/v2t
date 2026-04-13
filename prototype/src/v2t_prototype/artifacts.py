from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, TypeVar

from pydantic import BaseModel

from .full_video_asset_report import write_full_video_asset_report
from .models import (
    FullVideoAssetResult,
    LoadedStageBundle,
    LocalPreprocessingResult,
    RunManifest,
    StageErrorRecord,
    StageWarningsRecord,
    StageStatus,
    StageName,
    SegmentPrepResult,
    WarningItem,
)
from .preprocessing_report import write_preprocessing_report
from .segment_prep_report import write_segment_prep_report


LOCAL_PREPROCESSING_STAGE_DIR = "stage_01_local_preprocessing"
FULL_VIDEO_ASSET_STAGE_DIR = "stage_02_full_video_asset"
AGENT_A_STAGE_DIR = "stage_03_agent_a"
SEGMENT_PREP_STAGE_DIR = "stage_04_segment_prep"
AGENT_B_STAGE_DIR = "stage_05_agent_b"
AGENT_C_STAGE_DIR = "stage_06_agent_c"
FINAL_STAGE_DIR = "stage_07_final"
RUN_MANIFEST_FILENAME = "run_manifest.json"

ALL_STAGE_DIRS = [
    LOCAL_PREPROCESSING_STAGE_DIR,
    FULL_VIDEO_ASSET_STAGE_DIR,
    AGENT_A_STAGE_DIR,
    SEGMENT_PREP_STAGE_DIR,
    AGENT_B_STAGE_DIR,
    AGENT_C_STAGE_DIR,
    FINAL_STAGE_DIR,
]
T = TypeVar("T")


class RunManifestLoadError(RuntimeError):
    pass


class StageArtifactLoadError(RuntimeError):
    pass


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def generate_run_id(video_path: Path) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stem = re.sub(r"[^a-z0-9]", "_", video_path.stem.lower())[:20]
    suffix = hashlib.sha1(str(video_path.expanduser().resolve()).encode("utf-8")).hexdigest()[:6]
    return f"{timestamp}_{stem}_{suffix}"


def _build_default_manifest(*, run_id: str, video_path: str) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        video_path=video_path,
        created_at_utc=_utc_now_z(),
        entry_stage=None,
        stages=[
            StageStatus(stage=stage_dir, stage_dir=stage_dir, status="pending")
            for stage_dir in ALL_STAGE_DIRS
        ],
    )


def load_run_manifest(run_dir: Path) -> RunManifest:
    manifest_path = run_dir / RUN_MANIFEST_FILENAME
    try:
        return RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RunManifestLoadError(f"Run manifest not found: {manifest_path}") from exc
    except Exception as exc:
        raise RunManifestLoadError(f"Failed to load run manifest: {manifest_path}") from exc


def write_run_manifest(run_dir: Path, manifest: RunManifest) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / RUN_MANIFEST_FILENAME
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest_path


def ensure_run_manifest(*, runs_dir: Path, run_id: str, video_path: str) -> tuple[Path, RunManifest]:
    run_dir = runs_dir / run_id
    manifest_path = run_dir / RUN_MANIFEST_FILENAME
    if manifest_path.exists():
        manifest = load_run_manifest(run_dir)
        if manifest.video_path != video_path:
            raise ValueError(
                f"Run manifest video_path mismatch for run_id={run_id}: "
                f"{manifest.video_path} != {video_path}"
            )
        return run_dir, manifest

    manifest = _build_default_manifest(run_id=run_id, video_path=video_path)
    write_run_manifest(run_dir, manifest)
    return run_dir, manifest


def mark_stage_completed(manifest: RunManifest, *, stage_dir: str) -> RunManifest:
    timestamp = _utc_now_z()
    updated_stages: list[StageStatus] = []
    for stage in manifest.stages:
        if stage.stage == stage_dir:
            updated_stages.append(
                stage.model_copy(
                    update={
                        "status": "completed",
                        "started_at": stage.started_at or timestamp,
                        "completed_at": timestamp,
                        "error_message": None,
                    }
                )
            )
        else:
            updated_stages.append(stage)
    return manifest.model_copy(update={"stages": updated_stages})


def mark_stage_failed(
    manifest: RunManifest,
    *,
    stage_dir: str,
    error_message: str,
) -> RunManifest:
    timestamp = _utc_now_z()
    updated_stages: list[StageStatus] = []
    for stage in manifest.stages:
        if stage.stage == stage_dir:
            updated_stages.append(
                stage.model_copy(
                    update={
                        "status": "failed",
                        "started_at": stage.started_at or timestamp,
                        "completed_at": timestamp,
                        "error_message": error_message,
                    }
                )
            )
        else:
            updated_stages.append(stage)
    return manifest.model_copy(update={"stages": updated_stages})


def get_stage_dir(run_dir: Path, stage: StageName) -> Path:
    return run_dir / stage


def get_stage_status(manifest: RunManifest, stage: StageName) -> StageStatus:
    for stage_status in manifest.stages:
        if stage_status.stage == stage:
            return stage_status
    raise StageArtifactLoadError(f"Stage {stage} was not found in run manifest")


def _load_warnings_envelope(stage_dir: Path, stage: StageName) -> tuple[Path | None, list[WarningItem]]:
    warnings_path = stage_dir / "warnings.json"
    if not warnings_path.exists():
        return None, []

    try:
        envelope = StageWarningsRecord.model_validate_json(warnings_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise StageArtifactLoadError(f"Failed to load warnings from {warnings_path}") from exc

    if envelope.stage != stage:
        raise StageArtifactLoadError(
            f"Warnings stage mismatch: expected {stage}, found {envelope.stage}"
        )
    return warnings_path, envelope.warnings


def _load_stage_error(stage_dir: Path, stage: StageName) -> tuple[Path | None, StageErrorRecord | None]:
    error_path = stage_dir / "error.json"
    if not error_path.exists():
        return None, None

    try:
        error = StageErrorRecord.model_validate_json(error_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise StageArtifactLoadError(f"Failed to load stage error from {error_path}") from exc

    if error.stage != stage:
        raise StageArtifactLoadError(f"Stage error mismatch: expected {stage}, found {error.stage}")
    return error_path, error


def load_stage_bundle(
    run_dir: Path,
    stage: StageName,
    model_class: type[T] | None = None,
) -> LoadedStageBundle[T]:
    manifest = load_run_manifest(run_dir)
    stage_status = get_stage_status(manifest, stage)
    stage_dir = get_stage_dir(run_dir, stage)
    warnings_path, warnings = _load_warnings_envelope(stage_dir, stage)
    error_path, error = _load_stage_error(stage_dir, stage)
    report_path = stage_dir / "report.html"

    output: T | None = None
    output_path: Path | None = None
    if stage_status.status == "completed":
        if model_class is None:
            raise StageArtifactLoadError(f"model_class is required to load completed stage {stage}")
        output_path = stage_dir / "output.json"
        if not output_path.exists():
            raise StageArtifactLoadError(f"Completed stage output is missing: {output_path}")
        try:
            output = model_class.model_validate_json(output_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise StageArtifactLoadError(f"Failed to load stage output from {output_path}") from exc

    return LoadedStageBundle(
        stage=stage,
        status=stage_status.status,
        stage_dir=stage_dir,
        output_path=output_path if output_path and output_path.exists() else None,
        warnings_path=warnings_path,
        error_path=error_path,
        report_path=report_path if report_path.exists() else None,
        output=output,
        warnings=warnings,
        error=error,
    )


def require_completed_stage_output(
    run_dir: Path,
    stage: StageName,
    model_class: type[T],
) -> T:
    bundle = load_stage_bundle(run_dir, stage, model_class)
    if bundle.status != "completed" or bundle.output is None:
        raise StageArtifactLoadError(f"Stage {stage} is not available as completed output")
    return bundle.output


class StageArtifacts:
    def __init__(self, stage_dir: Path):
        self.stage_dir = stage_dir
        self.stage_dir.mkdir(parents=True, exist_ok=True)

    def write_output(self, data: BaseModel) -> Path:
        output_path = self.stage_dir / "output.json"
        output_path.write_text(data.model_dump_json(indent=2), encoding="utf-8")
        return output_path

    def write_warnings(self, *, stage: str, warnings: Sequence[WarningItem]) -> Path:
        warnings_path = self.stage_dir / "warnings.json"
        payload = {
            "stage": stage,
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "warnings": [item.model_dump(mode="json") for item in warnings],
        }
        warnings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return warnings_path

    def write_error(self, error: StageErrorRecord) -> Path:
        error_path = self.stage_dir / "error.json"
        error_path.write_text(error.model_dump_json(indent=2), encoding="utf-8")
        return error_path

    def write_report(
        self,
        result: LocalPreprocessingResult | FullVideoAssetResult | SegmentPrepResult,
        *,
        title: str | None = None,
        warnings: Sequence[WarningItem] | None = None,
        source_video_path: str | None = None,
    ) -> Path:
        report_path = self.stage_dir / "report.html"
        if isinstance(result, LocalPreprocessingResult):
            return write_preprocessing_report(
                result,
                report_path,
                title=title,
                warnings=warnings,
            )
        if isinstance(result, SegmentPrepResult):
            if source_video_path is None:
                raise ValueError("source_video_path is required for SegmentPrepResult reports")
            return write_segment_prep_report(
                result,
                report_path,
                source_video_path=source_video_path,
                title=title,
            )
        return write_full_video_asset_report(
            result,
            report_path,
            title=title,
            warnings=warnings,
        )


def write_local_preprocessing_artifacts(
    result: LocalPreprocessingResult,
    *,
    runs_dir: Path = Path("runs"),
    run_id: str | None = None,
    warnings: Sequence[WarningItem] | None = None,
    report_title: str | None = None,
) -> Path:
    actual_run_id = run_id or generate_run_id(Path(result.video_path))
    run_dir, manifest = ensure_run_manifest(
        runs_dir=runs_dir,
        run_id=actual_run_id,
        video_path=result.video_path,
    )
    stage_dir = run_dir / LOCAL_PREPROCESSING_STAGE_DIR
    artifacts = StageArtifacts(stage_dir)
    artifacts.write_output(result)
    artifacts.write_warnings(stage=LOCAL_PREPROCESSING_STAGE_DIR, warnings=list(warnings or []))
    artifacts.write_report(result, title=report_title, warnings=warnings)
    write_run_manifest(run_dir, mark_stage_completed(manifest, stage_dir=LOCAL_PREPROCESSING_STAGE_DIR))
    return stage_dir


def write_full_video_asset_artifacts(
    result: FullVideoAssetResult,
    *,
    runs_dir: Path = Path("runs"),
    run_id: str | None = None,
    warnings: Sequence[WarningItem] | None = None,
    report_title: str | None = None,
) -> Path:
    actual_run_id = run_id or generate_run_id(Path(result.local.video_path))
    run_dir, manifest = ensure_run_manifest(
        runs_dir=runs_dir,
        run_id=actual_run_id,
        video_path=result.local.video_path,
    )
    stage_dir = run_dir / FULL_VIDEO_ASSET_STAGE_DIR
    artifacts = StageArtifacts(stage_dir)
    artifacts.write_output(result)
    artifacts.write_warnings(stage=FULL_VIDEO_ASSET_STAGE_DIR, warnings=list(warnings or []))
    artifacts.write_report(result, title=report_title, warnings=warnings)
    write_run_manifest(run_dir, mark_stage_completed(manifest, stage_dir=FULL_VIDEO_ASSET_STAGE_DIR))
    return stage_dir


def write_segment_prep_artifacts(
    result: SegmentPrepResult,
    *,
    runs_dir: Path = Path("runs"),
    run_id: str | None = None,
    video_path: str,
    warnings: Sequence[WarningItem] | None = None,
    report_title: str | None = None,
) -> Path:
    actual_run_id = run_id or generate_run_id(Path(video_path))
    run_dir, manifest = ensure_run_manifest(
        runs_dir=runs_dir,
        run_id=actual_run_id,
        video_path=video_path,
    )
    stage_dir = run_dir / SEGMENT_PREP_STAGE_DIR
    artifacts = StageArtifacts(stage_dir)
    artifacts.write_output(result)
    artifacts.write_warnings(
        stage=SEGMENT_PREP_STAGE_DIR,
        warnings=list(warnings or result.warnings),
    )
    artifacts.write_report(
        result,
        title=report_title,
        warnings=warnings,
        source_video_path=video_path,
    )
    write_run_manifest(run_dir, mark_stage_completed(manifest, stage_dir=SEGMENT_PREP_STAGE_DIR))
    return stage_dir


def write_stage_failure_artifacts(
    *,
    stage: StageName,
    video_path: str,
    error: StageErrorRecord,
    runs_dir: Path = Path("runs"),
    run_id: str | None = None,
    warnings: Sequence[WarningItem] | None = None,
) -> Path:
    actual_run_id = run_id or generate_run_id(Path(video_path))
    run_dir, manifest = ensure_run_manifest(
        runs_dir=runs_dir,
        run_id=actual_run_id,
        video_path=video_path,
    )
    stage_dir = run_dir / stage
    artifacts = StageArtifacts(stage_dir)
    artifacts.write_warnings(stage=stage, warnings=list(warnings or []))
    artifacts.write_error(error)
    write_run_manifest(
        run_dir,
        mark_stage_failed(
            manifest,
            stage_dir=stage,
            error_message=error.message,
        ),
    )
    return stage_dir
