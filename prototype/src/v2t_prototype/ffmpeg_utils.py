from __future__ import annotations

import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from shutil import which
from typing import Iterator

import imageio_ffmpeg


def resolve_ffmpeg_bin(ffmpeg_bin: str = "ffmpeg") -> str:
    """Resolve an ffmpeg executable path, preferring the system binary and falling back to the uv-managed one."""
    discovered = which(ffmpeg_bin)
    if discovered:
        return discovered
    if ffmpeg_bin != "ffmpeg":
        return ffmpeg_bin
    return imageio_ffmpeg.get_ffmpeg_exe()


def _build_silent_copy_command(
    *,
    ffmpeg_bin: str,
    source_video_path: Path,
    output_path: Path,
) -> list[str]:
    return [
        ffmpeg_bin,
        "-y",
        "-i",
        str(source_video_path),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-an",
        str(output_path),
    ]


@contextmanager
def temporary_silent_video(
    source_video_path: Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
) -> Iterator[Path]:
    resolved_source_path = source_video_path.expanduser().resolve()
    resolved_ffmpeg_bin = resolve_ffmpeg_bin(ffmpeg_bin)
    suffix = resolved_source_path.suffix or ".mp4"

    with tempfile.TemporaryDirectory(prefix="v2t_silent_upload_") as temp_dir:
        silent_path = Path(temp_dir) / f"{resolved_source_path.stem}__silent{suffix}"
        subprocess.run(
            _build_silent_copy_command(
                ffmpeg_bin=resolved_ffmpeg_bin,
                source_video_path=resolved_source_path,
                output_path=silent_path,
            ),
            check=True,
            capture_output=True,
            text=True,
        )
        yield silent_path
