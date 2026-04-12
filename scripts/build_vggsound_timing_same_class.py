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


YOUTUBE_URL = "https://www.youtube.com/watch?v={video_id}"

TIMING_FOCUSED_LABELS = [
    "playing table tennis",
    "basketball bounce",
    "people marching",
    "people clapping",
    "playing cymbal",
    "playing bass drum",
    "hammering nails",
    "car engine starting",
    "fireworks banging",
    "door slamming",
    "opening or closing drawers",
    "machine gun shooting",
    "golf driving",
    "playing congas",
    "playing djembe",
    "chopping wood",
    "people screaming",
    "children shouting",
    "dog howling",
    "playing tennis",
]


@dataclass(frozen=True)
class ClipCandidate:
    label: str
    youtube_id: str
    start_sec: int
    split: str
    fallback_rank: int

    @property
    def url(self) -> str:
        return YOUTUBE_URL.format(video_id=self.youtube_id)


@dataclass(frozen=True)
class DownloadedClip:
    class_index: int
    member: str
    label: str
    youtube_id: str
    start_sec: int
    split: str
    path: Path
    duration_sec: float
    url: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build timing-focused same-class VGGSound alternating videos."
    )
    parser.add_argument(
        "--csv-path",
        type=Path,
        default=Path("data/vggsound/vggsound.csv"),
        help="Path to the VGGSound metadata CSV.",
    )
    parser.add_argument(
        "--vendor-path",
        type=Path,
        default=Path(".vendor"),
        help="Path containing vendored yt-dlp.",
    )
    parser.add_argument(
        "--root-dir",
        type=Path,
        default=Path("timing-focused"),
        help="Root directory for timing-focused outputs.",
    )
    parser.add_argument(
        "--target-classes",
        type=int,
        default=10,
        help="How many classes to build.",
    )
    parser.add_argument(
        "--clip-duration",
        type=int,
        default=10,
        help="Seconds to extract from each source row.",
    )
    parser.add_argument(
        "--segment-duration",
        type=float,
        default=5.0,
        help="Seconds per A/B segment in the final output.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260410,
        help="Random seed for deterministic fallback ordering.",
    )
    parser.add_argument(
        "--fallbacks-per-label",
        type=int,
        default=8,
        help="How many distinct source videos to stage for each label.",
    )
    parser.add_argument(
        "--max-source-duration",
        type=int,
        default=900,
        help="Skip source videos longer than this many seconds.",
    )
    return parser.parse_args()


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "clip"


def run_command(
    cmd: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        check=False,
    )


def ffprobe_duration(path: Path) -> float:
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}:\n{result.stdout}")
    return float(result.stdout.strip())


def build_ytdlp_env(vendor_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    vendor_str = str(vendor_path.resolve())
    env["PYTHONPATH"] = vendor_str + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    return env


def probe_youtube_duration(candidate: ClipCandidate, *, env: dict[str, str]) -> float | None:
    result = run_command(
        [
            sys.executable,
            "-m",
            "yt_dlp",
            "--skip-download",
            "--print",
            "%(duration)s",
            "--extractor-args",
            "youtube:player_client=android,web",
            candidate.url,
        ],
        env=env,
    )
    if result.returncode != 0:
        print(
            f"[probe-fail] {candidate.label} ({candidate.youtube_id})\n{result.stdout}",
            file=sys.stderr,
        )
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    duration = lines[-1]
    if duration.upper() == "NA":
        return None
    try:
        return float(duration)
    except ValueError:
        return None


def find_media_file(directory: Path) -> Path | None:
    media_suffixes = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
    candidates = [path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in media_suffixes]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_size)


def load_candidates(
    csv_path: Path, *, seed: int, fallbacks_per_label: int
) -> list[tuple[str, list[ClipCandidate]]]:
    rows_by_label_and_video: dict[str, dict[str, list[tuple[int, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    with csv_path.open(newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) != 4:
                continue
            youtube_id, start_sec, label, split = row
            if label not in TIMING_FOCUSED_LABELS:
                continue
            try:
                start_value = int(start_sec)
            except ValueError:
                continue
            rows_by_label_and_video[label][youtube_id].append((start_value, split))

    rng = random.Random(seed)
    staged: list[tuple[str, list[ClipCandidate]]] = []
    for label in TIMING_FOCUSED_LABELS:
        by_video = rows_by_label_and_video.get(label, {})
        video_ids = list(by_video.keys())
        rng.shuffle(video_ids)
        candidates: list[ClipCandidate] = []
        for fallback_rank, youtube_id in enumerate(video_ids[:fallbacks_per_label], start=1):
            starts = sorted(by_video[youtube_id], key=lambda item: item[0])
            start_sec, split = starts[0]
            candidates.append(
                ClipCandidate(
                    label=label,
                    youtube_id=youtube_id,
                    start_sec=start_sec,
                    split=split,
                    fallback_rank=fallback_rank,
                )
            )
        if candidates:
            staged.append((label, candidates))
    return staged


def download_and_normalize(
    candidate: ClipCandidate,
    *,
    class_index: int,
    member: str,
    clip_duration: int,
    max_source_duration: int,
    clips_dir: Path,
    temp_root: Path,
    env: dict[str, str],
) -> DownloadedClip | None:
    base_name = (
        f"{class_index:02d}_{slugify(candidate.label)}"
        f"__{member}__{candidate.youtube_id}_{candidate.start_sec}_{clip_duration}s"
    )
    output_path = clips_dir / f"{base_name}.mp4"
    if output_path.exists():
        return DownloadedClip(
            class_index=class_index,
            member=member,
            label=candidate.label,
            youtube_id=candidate.youtube_id,
            start_sec=candidate.start_sec,
            split=candidate.split,
            path=output_path,
            duration_sec=ffprobe_duration(output_path),
            url=candidate.url,
        )

    source_duration = probe_youtube_duration(candidate, env=env)
    if source_duration is None:
        return None
    if source_duration > max_source_duration:
        print(
            f"[skip-long] {candidate.label} ({candidate.youtube_id}) -> {source_duration:.1f}s",
            file=sys.stderr,
        )
        return None
    if candidate.start_sec + clip_duration > source_duration:
        print(
            f"[skip-short-tail] {candidate.label} ({candidate.youtube_id}) start={candidate.start_sec}s duration={source_duration:.1f}s",
            file=sys.stderr,
        )
        return None

    temp_dir = temp_root / base_name
    temp_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(temp_dir / "source.%(ext)s")
    download = run_command(
        [
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
        ],
        env=env,
    )
    if download.returncode != 0:
        print(
            f"[download-fail] {candidate.label} ({candidate.youtube_id})\n{download.stdout}",
            file=sys.stderr,
        )
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None

    source_media = find_media_file(temp_dir)
    if source_media is None:
        print(f"[download-fail] no media file for {candidate.youtube_id}", file=sys.stderr)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None

    normalize = run_command(
        [
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
    )
    shutil.rmtree(temp_dir, ignore_errors=True)
    if normalize.returncode != 0:
        print(
            f"[normalize-fail] {candidate.label} ({candidate.youtube_id})\n{normalize.stdout}",
            file=sys.stderr,
        )
        output_path.unlink(missing_ok=True)
        return None

    duration_sec = ffprobe_duration(output_path)
    if duration_sec < max(3.0, clip_duration * 0.85):
        print(
            f"[duration-fail] {candidate.label} ({candidate.youtube_id}) -> {duration_sec:.2f}s",
            file=sys.stderr,
        )
        output_path.unlink(missing_ok=True)
        return None

    return DownloadedClip(
        class_index=class_index,
        member=member,
        label=candidate.label,
        youtube_id=candidate.youtube_id,
        start_sec=candidate.start_sec,
        split=candidate.split,
        path=output_path,
        duration_sec=duration_sec,
        url=candidate.url,
    )


def make_alternating_video(
    clip_a: DownloadedClip,
    clip_b: DownloadedClip,
    *,
    segment_duration: float,
    outputs_dir: Path,
) -> tuple[Path, float]:
    safe_segment = min(segment_duration, clip_a.duration_sec, clip_b.duration_sec)
    if safe_segment <= 0:
        raise RuntimeError("segment_duration must be positive")

    output_path = outputs_dir / (
        f"{clip_a.class_index:02d}_{slugify(clip_a.label)}__same_class_abab_{safe_segment:g}s.mp4"
    )
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
    result = run_command(
        [
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
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to build alternating video for {clip_a.label}:\n{result.stdout}"
        )
    return output_path, ffprobe_duration(output_path)


def write_selected_manifest(path: Path, clips: list[DownloadedClip]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "class_index",
                "member",
                "label",
                "youtube_id",
                "start_sec",
                "split",
                "duration_sec",
                "path",
                "url",
            ]
        )
        for clip in clips:
            writer.writerow(
                [
                    clip.class_index,
                    clip.member,
                    clip.label,
                    clip.youtube_id,
                    clip.start_sec,
                    clip.split,
                    f"{clip.duration_sec:.3f}",
                    clip.path.as_posix(),
                    clip.url,
                ]
            )


def write_output_manifest(
    path: Path, rows: list[tuple[int, str, str, str, str, float]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["class_index", "label", "clip_a_path", "clip_b_path", "output_path", "duration_sec"]
        )
        for row in rows:
            class_index, label, clip_a_path, clip_b_path, output_path, duration_sec = row
            writer.writerow(
                [
                    class_index,
                    label,
                    clip_a_path,
                    clip_b_path,
                    output_path,
                    f"{duration_sec:.3f}",
                ]
            )


def main() -> None:
    args = parse_args()
    if args.target_classes <= 0:
        raise SystemExit("target_classes must be positive")
    if not (10 <= args.clip_duration <= 30):
        raise SystemExit("clip_duration must be between 10 and 30 seconds")
    if args.segment_duration <= 0:
        raise SystemExit("segment_duration must be positive")
    if not args.csv_path.exists():
        raise SystemExit(f"CSV not found: {args.csv_path}")
    if not args.vendor_path.exists():
        raise SystemExit(f"Vendored yt-dlp path not found: {args.vendor_path}")

    clips_dir = args.root_dir / "clips"
    outputs_dir = args.root_dir / "outputs"
    manifests_dir = args.root_dir / "manifests"
    selected_manifest = manifests_dir / "selected_clips.csv"
    output_manifest = manifests_dir / "edited_outputs.csv"
    temp_root = clips_dir / "_tmp"
    clips_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)

    env = build_ytdlp_env(args.vendor_path)
    staged_by_label = load_candidates(
        args.csv_path, seed=args.seed, fallbacks_per_label=args.fallbacks_per_label
    )

    selected_clips: list[DownloadedClip] = []
    successful_labels: list[str] = []
    for label, candidates in staged_by_label:
        if len(successful_labels) >= args.target_classes:
            break
        print(f"[label] trying {label!r} with {len(candidates)} staged candidates")
        class_index = len(successful_labels) + 1
        pair: list[DownloadedClip] = []
        used_video_ids: set[str] = set()
        for member_index, candidate in enumerate(candidates, start=1):
            if len(pair) >= 2:
                break
            if candidate.youtube_id in used_video_ids:
                continue
            member = "A" if len(pair) == 0 else "B"
            print(
                f"[candidate] class={class_index:02d} member={member} label={label!r} "
                f"id={candidate.youtube_id} start={candidate.start_sec}s"
            )
            clip = download_and_normalize(
                candidate,
                class_index=class_index,
                member=member,
                clip_duration=args.clip_duration,
                max_source_duration=args.max_source_duration,
                clips_dir=clips_dir,
                temp_root=temp_root,
                env=env,
            )
            if clip is None:
                continue
            pair.append(clip)
            used_video_ids.add(candidate.youtube_id)
            print(
                f"[selected] class={class_index:02d} member={member} "
                f"{label} -> {clip.path} ({clip.duration_sec:.2f}s)"
            )
        if len(pair) < 2:
            print(f"[label-skip] could not secure two clips for {label!r}", file=sys.stderr)
            continue
        selected_clips.extend(pair)
        successful_labels.append(label)

    if len(successful_labels) < args.target_classes:
        raise SystemExit(
            f"Only built {len(successful_labels)} classes out of {args.target_classes}."
        )

    write_selected_manifest(selected_manifest, selected_clips)
    print(f"Wrote selected clip manifest to {selected_manifest}")

    output_rows: list[tuple[int, str, str, str, str, float]] = []
    for class_index in range(1, args.target_classes + 1):
        pair = [clip for clip in selected_clips if clip.class_index == class_index]
        if len(pair) != 2:
            raise RuntimeError(f"Expected exactly 2 clips for class index {class_index}")
        clip_a = next(clip for clip in pair if clip.member == "A")
        clip_b = next(clip for clip in pair if clip.member == "B")
        output_path, duration_sec = make_alternating_video(
            clip_a, clip_b, segment_duration=args.segment_duration, outputs_dir=outputs_dir
        )
        output_rows.append(
            (
                class_index,
                clip_a.label,
                clip_a.path.as_posix(),
                clip_b.path.as_posix(),
                output_path.as_posix(),
                duration_sec,
            )
        )
        print(
            f"[output] class={class_index:02d} label={clip_a.label!r} -> "
            f"{output_path} ({duration_sec:.2f}s)"
        )

    write_output_manifest(output_manifest, output_rows)
    print(f"Wrote output manifest to {output_manifest}")


if __name__ == "__main__":
    main()
