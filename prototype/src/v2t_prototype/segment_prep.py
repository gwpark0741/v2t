from __future__ import annotations

import subprocess
from mimetypes import guess_type
from pathlib import Path
from typing import Optional

from google import genai

from .ffmpeg_utils import resolve_ffmpeg_bin as _resolve_ffmpeg_bin
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
    strip_audio: bool = True,
) -> list[str]:
    duration = end_time - start_time
    command = [
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
    ]
    if strip_audio:
        command.append("-an")
    else:
        command.extend(["-c:a", "aac", "-b:a", "128k"])
    command.append(str(output_path))
    return command


def resolve_ffmpeg_bin(ffmpeg_bin: str = "ffmpeg") -> str:
    return _resolve_ffmpeg_bin(ffmpeg_bin)


def run_segment_prep(
    full_video_asset: FullVideoAssetResult,
    *,
    ffmpeg_bin: str = "ffmpeg",
    clips_dir: Path,
    client: Optional[genai.Client] = None,
    strip_audio: bool = True,
) -> SegmentPrepResult:
    """Create and upload cut-level clips for downstream Agent B."""
    source_video_path = Path(full_video_asset.local.video_path)
    runtime_client = client or create_gemini_client()
    resolved_ffmpeg_bin = resolve_ffmpeg_bin(ffmpeg_bin)
    clips_dir.mkdir(parents=True, exist_ok=True)

    successful_clips: list[SegmentClip] = []
    skipped_cuts: list[SkippedCut] = []
    warnings: list[WarningItem] = []

    for cut in full_video_asset.local.cuts:
        clip_output_path = _build_clip_path(clips_dir, cut.id)

        ffmpeg_cmd = _build_ffmpeg_command(
            ffmpeg_bin=resolved_ffmpeg_bin,
            source_video_path=source_video_path,
            start_time=cut.start_time,
            end_time=cut.end_time,
            output_path=clip_output_path,
            strip_audio=strip_audio,
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
            )
        )

    return SegmentPrepResult(
        clips=successful_clips,
        skipped_cuts=skipped_cuts,
        warnings=warnings,
    )
