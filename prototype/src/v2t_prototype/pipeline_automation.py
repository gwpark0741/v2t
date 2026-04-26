from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal, Sequence

from .agent_a_runtime import DEFAULT_AGENT_A_MODEL, run_agent_a_runtime
from .agent_b_runtime import DEFAULT_AGENT_B_MODEL, run_agent_b_all_cuts_parallel
from .agent_c import run_agent_c
from .artifacts import (
    AGENT_A_STAGE_DIR,
    AGENT_B_STAGE_DIR,
    AGENT_C_STAGE_DIR,
    FULL_VIDEO_ASSET_STAGE_DIR,
    LOCAL_PREPROCESSING_STAGE_DIR,
    SEGMENT_PREP_STAGE_DIR,
    ensure_run_manifest,
    generate_run_id,
    write_agent_a_artifacts,
    write_agent_b_artifacts,
    write_agent_c_artifacts,
    write_full_video_asset_artifacts,
    write_local_preprocessing_artifacts,
    write_segment_prep_artifacts,
    write_stage_failure_artifacts,
)
from .gemini_client import create_gemini_client
from .models import StageErrorRecord, StageName
from .preprocessing import (
    collect_local_preprocessing_warnings,
    prepare_full_video_asset,
    run_local_preprocessing,
)
from .report_generator import generate_pipeline_report
from .segment_prep import run_segment_prep
from .track_judge import DEFAULT_TRACK_JUDGE_MODEL


VIDEO_EXTENSIONS = {
    ".avi",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".webm",
}


@dataclass(frozen=True)
class PipelineAutomationResult:
    video_path: Path
    run_id: str
    run_dir: Path
    status: Literal["completed", "failed"]
    report_path: Path | None = None
    failed_stage: StageName | None = None
    error_message: str | None = None


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _error_message(exc: Exception) -> str:
    message = str(exc).strip()
    return message or exc.__class__.__name__


def _build_stage_error(*, stage: StageName, video_path: Path, exc: Exception) -> StageErrorRecord:
    return StageErrorRecord(
        stage=stage,
        failed_at_utc=_utc_now_z(),
        attempt_count=1,
        retryable=False,
        error_class=exc.__class__.__name__,
        message=_error_message(exc),
        context={"video_path": str(video_path)},
    )


def _try_generate_report(run_dir: Path) -> Path | None:
    try:
        return generate_pipeline_report(run_dir)
    except Exception:
        return None


def discover_video_paths(inputs: Sequence[Path], *, recursive: bool = False) -> list[Path]:
    discovered: list[Path] = []
    seen: set[Path] = set()

    for input_path in inputs:
        resolved = input_path.expanduser().resolve()
        if resolved.is_file():
            candidates = [resolved] if resolved.suffix.lower() in VIDEO_EXTENSIONS else []
        elif resolved.is_dir():
            iterator: Iterable[Path]
            iterator = resolved.rglob("*") if recursive else resolved.iterdir()
            candidates = [
                path.resolve()
                for path in iterator
                if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
            ]
        else:
            continue

        for candidate in sorted(candidates):
            if candidate in seen:
                continue
            seen.add(candidate)
            discovered.append(candidate)

    return discovered


def run_pipeline_for_video(
    video_path: Path,
    *,
    runs_dir: Path = Path("runs"),
    run_id: str | None = None,
    ffmpeg_bin: str = "ffmpeg",
    agent_a_model: str = DEFAULT_AGENT_A_MODEL,
    agent_b_model: str = DEFAULT_AGENT_B_MODEL,
    track_judge_model: str = DEFAULT_TRACK_JUDGE_MODEL,
    max_agent_b_concurrency: int = 5,
    max_agent_b_retries: int = 2,
    stop_on_error: bool = False,
) -> PipelineAutomationResult:
    resolved_video_path = video_path.expanduser().resolve()
    actual_run_id = run_id or generate_run_id(resolved_video_path)
    run_dir, _ = ensure_run_manifest(
        runs_dir=runs_dir,
        run_id=actual_run_id,
        video_path=str(resolved_video_path),
    )
    runtime_client = create_gemini_client()

    current_stage: StageName = LOCAL_PREPROCESSING_STAGE_DIR
    try:
        local = run_local_preprocessing(resolved_video_path)
        local_warnings = collect_local_preprocessing_warnings(local)
        write_local_preprocessing_artifacts(
            local,
            runs_dir=runs_dir,
            run_id=actual_run_id,
            warnings=local_warnings,
        )

        current_stage = FULL_VIDEO_ASSET_STAGE_DIR
        full_video_asset = prepare_full_video_asset(
            local,
            client=runtime_client,
            ffmpeg_bin=ffmpeg_bin,
        )
        write_full_video_asset_artifacts(
            full_video_asset,
            runs_dir=runs_dir,
            run_id=actual_run_id,
        )

        current_stage = AGENT_A_STAGE_DIR
        agent_a_output = run_agent_a_runtime(
            full_video_asset,
            client=runtime_client,
            model=agent_a_model,
        )
        write_agent_a_artifacts(
            agent_a_output,
            full_video_asset=full_video_asset,
            runs_dir=runs_dir,
            run_id=actual_run_id,
        )

        current_stage = SEGMENT_PREP_STAGE_DIR
        segment_prep = run_segment_prep(
            full_video_asset,
            ffmpeg_bin=ffmpeg_bin,
            clips_dir=run_dir / SEGMENT_PREP_STAGE_DIR / "clips",
            client=runtime_client,
        )
        write_segment_prep_artifacts(
            segment_prep,
            runs_dir=runs_dir,
            run_id=actual_run_id,
            video_path=str(resolved_video_path),
        )

        current_stage = AGENT_B_STAGE_DIR
        agent_b_result = asyncio.run(
            run_agent_b_all_cuts_parallel(
                segment_prep,
                agent_a_output,
                client=runtime_client,
                model=agent_b_model,
                max_concurrency=max_agent_b_concurrency,
                max_retries=max_agent_b_retries,
            )
        )
        write_agent_b_artifacts(
            agent_b_result,
            agent_a_output,
            video_path=str(resolved_video_path),
            runs_dir=runs_dir,
            run_id=actual_run_id,
        )

        current_stage = AGENT_C_STAGE_DIR
        agent_c_result = run_agent_c(
            agent_b_result,
            agent_a_output.response.entity_registry,
            flash_client=runtime_client,
            flash_model=track_judge_model,
        )
        write_agent_c_artifacts(
            agent_c_result,
            video_path=str(resolved_video_path),
            runs_dir=runs_dir,
            run_id=actual_run_id,
        )

        report_path = generate_pipeline_report(run_dir)
        return PipelineAutomationResult(
            video_path=resolved_video_path,
            run_id=actual_run_id,
            run_dir=run_dir,
            status="completed",
            report_path=report_path,
        )
    except Exception as exc:
        error = _build_stage_error(stage=current_stage, video_path=resolved_video_path, exc=exc)
        write_stage_failure_artifacts(
            stage=current_stage,
            video_path=str(resolved_video_path),
            error=error,
            runs_dir=runs_dir,
            run_id=actual_run_id,
        )
        report_path = _try_generate_report(run_dir)
        result = PipelineAutomationResult(
            video_path=resolved_video_path,
            run_id=actual_run_id,
            run_dir=run_dir,
            status="failed",
            report_path=report_path,
            failed_stage=current_stage,
            error_message=error.message,
        )
        if stop_on_error:
            raise
        return result


def run_pipeline_for_inputs(
    inputs: Sequence[Path],
    *,
    runs_dir: Path = Path("runs"),
    recursive: bool = False,
    ffmpeg_bin: str = "ffmpeg",
    agent_a_model: str = DEFAULT_AGENT_A_MODEL,
    agent_b_model: str = DEFAULT_AGENT_B_MODEL,
    track_judge_model: str = DEFAULT_TRACK_JUDGE_MODEL,
    max_agent_b_concurrency: int = 5,
    max_agent_b_retries: int = 2,
    stop_on_error: bool = False,
) -> list[PipelineAutomationResult]:
    results: list[PipelineAutomationResult] = []
    for video_path in discover_video_paths(inputs, recursive=recursive):
        result = run_pipeline_for_video(
            video_path,
            runs_dir=runs_dir,
            ffmpeg_bin=ffmpeg_bin,
            agent_a_model=agent_a_model,
            agent_b_model=agent_b_model,
            track_judge_model=track_judge_model,
            max_agent_b_concurrency=max_agent_b_concurrency,
            max_agent_b_retries=max_agent_b_retries,
            stop_on_error=stop_on_error,
        )
        results.append(result)
        if stop_on_error and result.status == "failed":
            break
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the full Stage 01-06 pipeline for one or more videos and store the usual run artifacts."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Video files and/or directories that contain video files.",
    )
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"), help="Root directory for run outputs.")
    parser.add_argument("--recursive", action="store_true", help="Recursively discover videos inside input directories.")
    parser.add_argument("--ffmpeg-bin", default="ffmpeg", help="ffmpeg executable to use for Stage 04 clip generation.")
    parser.add_argument("--agent-a-model", default=DEFAULT_AGENT_A_MODEL, help="Gemini model for Stage 03 Agent A.")
    parser.add_argument("--agent-b-model", default=DEFAULT_AGENT_B_MODEL, help="Gemini model for Stage 05 Agent B.")
    parser.add_argument(
        "--track-judge-model",
        default=DEFAULT_TRACK_JUDGE_MODEL,
        help="Gemini model for Stage 06 track grouping.",
    )
    parser.add_argument(
        "--max-agent-b-concurrency",
        type=int,
        default=5,
        help="Maximum cut-level concurrency for Stage 05 Agent B.",
    )
    parser.add_argument(
        "--max-agent-b-retries",
        type=int,
        default=2,
        help="Maximum retries for retryable Agent B generation failures.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop immediately when one video fails instead of continuing with the remaining inputs.",
    )
    args = parser.parse_args(argv)

    results = run_pipeline_for_inputs(
        args.inputs,
        runs_dir=args.runs_dir,
        recursive=args.recursive,
        ffmpeg_bin=args.ffmpeg_bin,
        agent_a_model=args.agent_a_model,
        agent_b_model=args.agent_b_model,
        track_judge_model=args.track_judge_model,
        max_agent_b_concurrency=args.max_agent_b_concurrency,
        max_agent_b_retries=args.max_agent_b_retries,
        stop_on_error=args.stop_on_error,
    )
    for result in results:
        if result.status == "completed":
            print(f"COMPLETED\t{result.run_id}\t{result.video_path}\t{result.run_dir}")
        else:
            failed_stage = result.failed_stage or "unknown_stage"
            error_message = result.error_message or "unknown error"
            print(f"FAILED\t{result.run_id}\t{failed_stage}\t{error_message}\t{result.video_path}")

    return 0 if all(result.status == "completed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
