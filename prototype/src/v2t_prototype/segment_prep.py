from __future__ import annotations

import subprocess
from shutil import which
from mimetypes import guess_type
from pathlib import Path
from typing import Optional

import imageio_ffmpeg
from google import genai

from .gemini_client import (
    create_gemini_client,
    get_uploaded_file_name,
    get_uploaded_video_url,
    upload_video_file,
    wait_for_uploaded_file_active,
)
from .models import FullVideoAssetResult, SegmentClip, SegmentPrepResult, SkippedCut, WarningItem


def _build_clip_path(clips_dir: Path, cut_id: str) -> Path:
    return clips_dir / f"{cut_id}.mp4"


def _build_ffmpeg_command(
    *,
    ffmpeg_bin: str,
    source_video_path: Path,
    start_time: float,
    end_time: float,
    output_path: Path,
) -> list[str]:
    duration = end_time - start_time
    return [
        ffmpeg_bin,
        "-y",
        "-ss",
        f"{start_time:.3f}",
        "-i",
        str(source_video_path),
        "-t",
        f"{duration:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        str(output_path),
    ]


def resolve_ffmpeg_bin(ffmpeg_bin: str = "ffmpeg") -> str:
    """Resolve an ffmpeg executable path, preferring the system binary and falling back to the uv-managed one."""
    discovered = which(ffmpeg_bin)
    if discovered:
        return discovered
    if ffmpeg_bin != "ffmpeg":
        return ffmpeg_bin
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_segment_prep(
    full_video_asset: FullVideoAssetResult,
    *,
    ffmpeg_bin: str = "ffmpeg",
    clips_dir: Path,
    client: Optional[genai.Client] = None,
    clip_padding_seconds: float = 0.5,
) -> SegmentPrepResult:
    """Create and upload cut-level clips for downstream Agent B."""
    source_video_path = Path(full_video_asset.local.video_path)
    video_duration = full_video_asset.local.video_metadata.duration_seconds
    runtime_client = client or create_gemini_client()
    resolved_ffmpeg_bin = resolve_ffmpeg_bin(ffmpeg_bin)
    clips_dir.mkdir(parents=True, exist_ok=True)

    successful_clips: list[SegmentClip] = []
    skipped_cuts: list[SkippedCut] = []
    warnings: list[WarningItem] = []

    for cut in full_video_asset.local.cuts:
        padded_start = max(0.0, cut.start_time - clip_padding_seconds)
        padded_end = min(video_duration, cut.end_time + clip_padding_seconds)
        actual_padding_start = round(cut.start_time - padded_start, 3)
        actual_padding_end = round(padded_end - cut.end_time, 3)
        clip_output_path = _build_clip_path(clips_dir, cut.id)

        ffmpeg_cmd = _build_ffmpeg_command(
            ffmpeg_bin=resolved_ffmpeg_bin,
            source_video_path=source_video_path,
            start_time=padded_start,
            end_time=padded_end,
            output_path=clip_output_path,
        )
        try:
            subprocess.run(
                ffmpeg_cmd,
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception as exc:
            skipped_cuts.append(
                SkippedCut(
                    cut_id=cut.id,
                    reason="FFMPEG_FAILURE",
                    error_detail=str(exc),
                )
            )
            warnings.append(
                WarningItem(
                    code="SEGMENT_PREP_FFMPEG_FAILURE",
                    severity="warning",
                    message="Clip extraction failed for one cut.",
                    context={"cut_id": cut.id, "error": str(exc)},
                )
            )
            continue

        try:
            uploaded_file = upload_video_file(runtime_client, clip_output_path)
            uploaded_file = wait_for_uploaded_file_active(runtime_client, uploaded_file)
            clip_video_url = get_uploaded_video_url(uploaded_file)
            clip_gemini_file_name = get_uploaded_file_name(uploaded_file)
        except TimeoutError as exc:
            skipped_cuts.append(
                SkippedCut(
                    cut_id=cut.id,
                    reason="UPLOAD_TIMEOUT",
                    error_detail=str(exc),
                )
            )
            warnings.append(
                WarningItem(
                    code="SEGMENT_PREP_UPLOAD_TIMEOUT",
                    severity="warning",
                    message="Clip upload did not reach ACTIVE within timeout.",
                    context={"cut_id": cut.id, "error": str(exc)},
                )
            )
            continue
        except Exception as exc:
            skipped_cuts.append(
                SkippedCut(
                    cut_id=cut.id,
                    reason="UPLOAD_FAILURE",
                    error_detail=str(exc),
                )
            )
            warnings.append(
                WarningItem(
                    code="SEGMENT_PREP_UPLOAD_FAILURE",
                    severity="warning",
                    message="Clip upload failed for one cut.",
                    context={"cut_id": cut.id, "error": str(exc)},
                )
            )
            continue

        mime_type, _ = guess_type(str(clip_output_path))
        successful_clips.append(
            SegmentClip(
                cut_id=cut.id,
                local_clip_path=str(clip_output_path.resolve()),
                clip_video_url=clip_video_url,
                clip_gemini_file_name=clip_gemini_file_name,
                clip_video_mime_type=mime_type or "video/mp4",
                padded_start_time=round(padded_start, 3),
                padded_end_time=round(padded_end, 3),
                actual_padding_start=actual_padding_start,
                actual_padding_end=actual_padding_end,
            )
        )

    return SegmentPrepResult(
        clips=successful_clips,
        skipped_cuts=skipped_cuts,
        warnings=warnings,
    )
