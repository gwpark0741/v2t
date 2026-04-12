#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import random
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


YOUTUBE_URL = "https://www.youtube.com/watch?v={video_id}"


@dataclass(frozen=True)
class ClipCandidate:
    youtube_id: str
    start_sec: int
    label: str
    split: str
    label_rank: int
    fallback_rank: int

    @property
    def url(self) -> str:
        return YOUTUBE_URL.format(video_id=self.youtube_id)


@dataclass(frozen=True)
class DownloadedClip:
    index: int
    label: str
    youtube_id: str
    start_sec: int
    split: str
    path: Path
    duration_sec: float
    url: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download VGGSound clips and build alternating a/b/a/b videos."
    )
    parser.add_argument(
        "--csv-path",
        type=Path,
        default=Path("data/vggsound/vggsound.csv"),
        help="Path to the VGGSound CSV metadata.",
    )
    parser.add_argument(
        "--vendor-path",
        type=Path,
        default=Path(".vendor"),
        help="Path containing the vendored yt-dlp Python package.",
    )
    parser.add_argument(
        "--clips-dir",
        type=Path,
        default=Path("clips/vggsound_raw"),
        help="Directory for normalized source clips.",
    )
    parser.add_argument(
        "--outputs-dir",
        type=Path,
        default=Path("outputs/vggsound_ab"),
        help="Directory for final alternating videos.",
    )
    parser.add_argument(
        "--selected-manifest",
        type=Path,
        default=Path("manifests/vggsound_selected.csv"),
        help="CSV manifest for the downloaded clips.",
    )
    parser.add_argument(
        "--outputs-manifest",
        type=Path,
        default=Path("manifests/vggsound_outputs.csv"),
        help="CSV manifest for the final edited outputs.",
    )
    parser.add_argument(
        "--target-clips",
        type=int,
        default=20,
        help="Number of source clips to download.",
    )
    parser.add_argument(
        "--target-outputs",
        type=int,
        default=10,
        help="Number of final a/b/a/b videos to create.",
    )
    parser.add_argument(
        "--clip-duration",
        type=int,
        default=10,
        help="Length in seconds to extract from each VGGSound source row (10-30 recommended).",
    )
    parser.add_argument(
        "--segment-duration",
        type=float,
        default=5.0,
        help="Length in seconds of each A/B segment in the final a/b/a/b video.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260410,
        help="Random seed for deterministic sampling.",
    )
    parser.add_argument(
        "--candidate-label-factor",
        type=int,
        default=8,
        help="How many distinct labels to stage as backup candidates, relative to target clips.",
    )
    parser.add_argument(
        "--fallbacks-per-label",
        type=int,
        default=3,
        help="How many clip rows to keep per label as fallbacks.",
    )
    parser.add_argument(
        "--max-source-duration",
        type=int,
        default=900,
        help="Skip source videos longer than this many seconds before downloading.",
    )
    return parser.parse_args()


def ensure_tools() -> None:
    for tool in ("ffmpeg", "ffprobe", sys.executable):
        if shutil.which(tool) is None and tool != sys.executable:
            raise SystemExit(f"Required tool not found: {tool}")


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "clip"


def run_command(
    cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def load_candidates(
    csv_path: Path,
    *,
    target_clips: int,
    seed: int,
    candidate_label_factor: int,
    fallbacks_per_label: int,
) -> list[ClipCandidate]:
    rows_by_label: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    with csv_path.open(newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) != 4:
                continue
            youtube_id, start_sec, label, split = row
            try:
                start_value = int(start_sec)
            except ValueError:
                continue
            rows_by_label[label].append((youtube_id, start_value, split))

    if not rows_by_label:
        raise SystemExit(f"No usable rows found in {csv_path}")

    rng = random.Random(seed)
    labels = list(rows_by_label.keys())
    rng.shuffle(labels)

    label_limit = min(len(labels), max(target_clips, target_clips * candidate_label_factor))
    chosen_labels = labels[:label_limit]

    candidates: list[ClipCandidate] = []
    for label_rank, label in enumerate(chosen_labels, start=1):
        rows = list(rows_by_label[label])
        rng.shuffle(rows)
        for fallback_rank, (youtube_id, start_sec, split) in enumerate(
            rows[:fallbacks_per_label], start=1
        ):
            candidates.append(
                ClipCandidate(
                    youtube_id=youtube_id,
                    start_sec=start_sec,
                    label=label,
                    split=split,
                    label_rank=label_rank,
                    fallback_rank=fallback_rank,
                )
            )
    return candidates


def find_media_file(directory: Path) -> Path | None:
    video_suffixes = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
    candidates = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in video_suffixes:
            continue
        candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = run_command(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}:\n{result.stdout}")
    return float(result.stdout.strip())


def probe_youtube_duration(
    candidate: ClipCandidate, *, vendor_path: Path
) -> float | None:
    env = os.environ.copy()
    vendor_str = str(vendor_path.resolve())
    env["PYTHONPATH"] = vendor_str + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    probe_cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--skip-download",
        "--print",
        "%(duration)s",
        "--extractor-args",
        "youtube:player_client=android,web",
        candidate.url,
    ]
    result = run_command(probe_cmd, env=env)
    if result.returncode != 0:
        print(
            f"[probe-fail] {candidate.label} ({candidate.youtube_id})\n{result.stdout}",
            file=sys.stderr,
        )
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    value = lines[-1]
    if value.upper() == "NA":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def download_and_normalize(
    candidate: ClipCandidate,
    *,
    index: int,
    clip_duration: int,
    max_source_duration: int,
    clips_dir: Path,
    vendor_path: Path,
    temp_root: Path,
) -> DownloadedClip | None:
    base_name = (
        f"{index:02d}_{slugify(candidate.label)}"
        f"__{candidate.youtube_id}_{candidate.start_sec}_{clip_duration}s"
    )
    output_path = clips_dir / f"{base_name}.mp4"
    if output_path.exists():
        duration_sec = ffprobe_duration(output_path)
        return DownloadedClip(
            index=index,
            label=candidate.label,
            youtube_id=candidate.youtube_id,
            start_sec=candidate.start_sec,
            split=candidate.split,
            path=output_path,
            duration_sec=duration_sec,
            url=candidate.url,
        )

    temp_dir = temp_root / base_name
    temp_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(temp_dir / "source.%(ext)s")

    source_duration = probe_youtube_duration(candidate, vendor_path=vendor_path)
    if source_duration is None:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None
    if source_duration > max_source_duration:
        print(
            f"[skip-long] {candidate.label} ({candidate.youtube_id}) -> "
            f"{source_duration:.1f}s",
            file=sys.stderr,
        )
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None
    if candidate.start_sec + clip_duration > source_duration:
        print(
            f"[skip-short-tail] {candidate.label} ({candidate.youtube_id}) -> "
            f"start {candidate.start_sec}s, duration {source_duration:.1f}s",
            file=sys.stderr,
        )
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None

    env = os.environ.copy()
    vendor_str = str(vendor_path.resolve())
    env["PYTHONPATH"] = vendor_str + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )

    download_cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--force-overwrites",
        "--no-continue",
        "--newline",
        "-f",
        "18/b[height<=360]/b",
        "--merge-output-format",
        "mp4",
        "--extractor-args",
        "youtube:player_client=android,web",
        "-o",
        outtmpl,
        candidate.url,
    ]
    result = run_command(download_cmd, env=env)
    if result.returncode != 0:
        print(
            f"[download-fail] {candidate.label} ({candidate.youtube_id})\n{result.stdout}",
            file=sys.stderr,
        )
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None

    source_media = find_media_file(temp_dir)
    if source_media is None:
        print(
            f"[download-fail] No media file produced for {candidate.youtube_id}",
            file=sys.stderr,
        )
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None

    normalize_cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(candidate.start_sec),
        "-i",
        str(source_media),
        "-t",
        str(clip_duration),
        "-vf",
        "scale=1280:720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
        "-r",
        "30",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    normalized = run_command(normalize_cmd)
    shutil.rmtree(temp_dir, ignore_errors=True)
    if normalized.returncode != 0:
        print(
            f"[normalize-fail] {candidate.label} ({candidate.youtube_id})\n{normalized.stdout}",
            file=sys.stderr,
        )
        output_path.unlink(missing_ok=True)
        return None

    duration_sec = ffprobe_duration(output_path)
    min_duration = max(3.0, clip_duration * 0.85)
    if duration_sec < min_duration:
        print(
            f"[duration-fail] {candidate.label} ({candidate.youtube_id}) -> {duration_sec:.2f}s",
            file=sys.stderr,
        )
        output_path.unlink(missing_ok=True)
        return None

    return DownloadedClip(
        index=index,
        label=candidate.label,
        youtube_id=candidate.youtube_id,
        start_sec=candidate.start_sec,
        split=candidate.split,
        path=output_path,
        duration_sec=duration_sec,
        url=candidate.url,
    )


def write_selected_manifest(path: Path, downloaded: Iterable[DownloadedClip]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "index",
                "label",
                "youtube_id",
                "start_sec",
                "split",
                "duration_sec",
                "path",
                "url",
            ]
        )
        for clip in downloaded:
            writer.writerow(
                [
                    clip.index,
                    clip.label,
                    clip.youtube_id,
                    clip.start_sec,
                    clip.split,
                    f"{clip.duration_sec:.3f}",
                    clip.path.as_posix(),
                    clip.url,
                ]
            )


def make_alternating_video(
    clip_a: DownloadedClip,
    clip_b: DownloadedClip,
    *,
    pair_index: int,
    segment_duration: float,
    outputs_dir: Path,
) -> tuple[Path, float]:
    safe_segment = min(segment_duration, clip_a.duration_sec, clip_b.duration_sec)
    if safe_segment <= 0:
        raise RuntimeError("Segment duration must be positive.")

    output_name = (
        f"{pair_index:02d}_{slugify(clip_a.label)}__{slugify(clip_b.label)}"
        f"_abab_{safe_segment:g}s.mp4"
    )
    output_path = outputs_dir / output_name

    filter_complex = (
        f"[0:v]trim=start=0:end={safe_segment},setpts=PTS-STARTPTS[v0a];"
        f"[0:a]atrim=start=0:end={safe_segment},asetpts=PTS-STARTPTS[a0a];"
        f"[1:v]trim=start=0:end={safe_segment},setpts=PTS-STARTPTS[v1a];"
        f"[1:a]atrim=start=0:end={safe_segment},asetpts=PTS-STARTPTS[a1a];"
        f"[0:v]trim=start=0:end={safe_segment},setpts=PTS-STARTPTS[v0b];"
        f"[0:a]atrim=start=0:end={safe_segment},asetpts=PTS-STARTPTS[a0b];"
        f"[1:v]trim=start=0:end={safe_segment},setpts=PTS-STARTPTS[v1b];"
        f"[1:a]atrim=start=0:end={safe_segment},asetpts=PTS-STARTPTS[a1b];"
        "[v0a][a0a][v1a][a1a][v0b][a0b][v1b][a1b]concat=n=4:v=1:a=1[v][a]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(clip_a.path),
        "-i",
        str(clip_b.path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = run_command(cmd)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to build alternating video for {clip_a.label} + {clip_b.label}:\n"
            f"{result.stdout}"
        )
    return output_path, ffprobe_duration(output_path)


def write_outputs_manifest(
    path: Path,
    rows: list[tuple[int, str, str, str, str, float]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "pair_index",
                "label_a",
                "label_b",
                "clip_a_path",
                "clip_b_path",
                "output_path",
                "duration_sec",
            ]
        )
        for pair_index, label_a, label_b, clip_a_path, clip_b_path, output_path, duration_sec in rows:
            writer.writerow(
                [
                    pair_index,
                    label_a,
                    label_b,
                    clip_a_path,
                    clip_b_path,
                    output_path,
                    f"{duration_sec:.3f}",
                ]
            )


def main() -> None:
    args = parse_args()
    if args.target_outputs * 2 > args.target_clips:
        raise SystemExit("target_outputs * 2 must be less than or equal to target_clips")
    if not (10 <= args.clip_duration <= 30):
        raise SystemExit("clip_duration must be between 10 and 30 seconds")
    if args.segment_duration <= 0:
        raise SystemExit("segment_duration must be positive")
    if not args.csv_path.exists():
        raise SystemExit(f"CSV not found: {args.csv_path}")
    if not args.vendor_path.exists():
        raise SystemExit(
            f"Vendored yt-dlp path not found: {args.vendor_path}. "
            "Install yt-dlp into that directory first."
        )

    ensure_tools()
    args.clips_dir.mkdir(parents=True, exist_ok=True)
    args.outputs_dir.mkdir(parents=True, exist_ok=True)
    temp_root = args.clips_dir / "_tmp"
    temp_root.mkdir(parents=True, exist_ok=True)

    candidates = load_candidates(
        args.csv_path,
        target_clips=args.target_clips,
        seed=args.seed,
        candidate_label_factor=args.candidate_label_factor,
        fallbacks_per_label=args.fallbacks_per_label,
    )
    print(f"Loaded {len(candidates)} staged candidates.")

    downloaded: list[DownloadedClip] = []
    used_labels: set[str] = set()
    for candidate in candidates:
        if len(downloaded) >= args.target_clips:
            break
        if candidate.label in used_labels:
            continue
        print(
            f"[candidate] label={candidate.label!r} id={candidate.youtube_id} "
            f"start={candidate.start_sec}s"
        )
        clip = download_and_normalize(
            candidate,
            index=len(downloaded) + 1,
            clip_duration=args.clip_duration,
            max_source_duration=args.max_source_duration,
            clips_dir=args.clips_dir,
            vendor_path=args.vendor_path,
            temp_root=temp_root,
        )
        if clip is None:
            continue
        downloaded.append(clip)
        used_labels.add(candidate.label)
        print(
            f"[selected] #{clip.index:02d} {clip.label} -> {clip.path} "
            f"({clip.duration_sec:.2f}s)"
        )

    if len(downloaded) < args.target_clips:
        raise SystemExit(
            f"Only downloaded {len(downloaded)} clips out of {args.target_clips}. "
            "Rerun with a different seed or higher candidate_label_factor/fallbacks_per_label."
        )

    write_selected_manifest(args.selected_manifest, downloaded)
    print(f"Wrote selected clip manifest to {args.selected_manifest}")

    output_rows: list[tuple[int, str, str, str, str, str, float]] = []
    for pair_index in range(1, args.target_outputs + 1):
        clip_a = downloaded[(pair_index - 1) * 2]
        clip_b = downloaded[(pair_index - 1) * 2 + 1]
        if clip_a.label == clip_b.label:
            raise RuntimeError("Expected different labels in each pair.")
        output_path, duration_sec = make_alternating_video(
            clip_a,
            clip_b,
            pair_index=pair_index,
            segment_duration=args.segment_duration,
            outputs_dir=args.outputs_dir,
        )
        output_rows.append(
            (
                pair_index,
                clip_a.label,
                clip_b.label,
                clip_a.path.as_posix(),
                clip_b.path.as_posix(),
                output_path.as_posix(),
                duration_sec,
            )
        )
        print(
            f"[output] #{pair_index:02d} {clip_a.label} / {clip_b.label} -> "
            f"{output_path} ({duration_sec:.2f}s)"
        )

    write_outputs_manifest(args.outputs_manifest, output_rows)
    print(f"Wrote output manifest to {args.outputs_manifest}")


if __name__ == "__main__":
    main()
