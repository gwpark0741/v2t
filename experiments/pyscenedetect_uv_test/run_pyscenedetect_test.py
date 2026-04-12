from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from time import perf_counter

from scenedetect import ContentDetector, SceneManager, open_video


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = REPO_ROOT / "videos"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "outputs" / "pyscenedetect_uv_test"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a minimal PySceneDetect cut test against videos in the repo "
            "and write per-video outputs to a separate directory."
        )
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--video",
        type=Path,
        help="Path to a single video file to analyze.",
    )
    source_group.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing input videos.",
    )
    parser.add_argument(
        "--glob",
        default="*.mp4",
        help="Glob used when --input-dir is selected.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where scene detection outputs will be written.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=27.0,
        help="ContentDetector threshold. Lower values create more cuts.",
    )
    parser.add_argument(
        "--min-scene-len",
        type=int,
        default=10,
        help="Minimum scene length in frames.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on the number of videos to process.",
    )
    return parser.parse_args()


def resolve_videos(args: argparse.Namespace) -> list[Path]:
    if args.video is not None:
        return [args.video.resolve()]

    input_dir = args.input_dir.resolve()
    if not input_dir.exists():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    videos = sorted(path.resolve() for path in input_dir.glob(args.glob) if path.is_file())
    if args.limit is not None:
        videos = videos[: args.limit]
    return videos


def detect_scenes(
    video_path: Path,
    output_dir: Path,
    threshold: float,
    min_scene_len: int,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    video_output_dir = output_dir / video_path.stem
    video_output_dir.mkdir(parents=True, exist_ok=True)

    video = open_video(str(video_path))
    scene_manager = SceneManager()
    scene_manager.add_detector(
        ContentDetector(threshold=threshold, min_scene_len=min_scene_len)
    )

    started_at = perf_counter()
    scene_manager.detect_scenes(video=video, show_progress=True)
    elapsed_sec = perf_counter() - started_at

    scene_list = scene_manager.get_scene_list(start_in_scene=True)
    cut_list = [start for start, _ in scene_list[1:]]
    total_duration_sec = scene_list[-1][1].get_seconds() if scene_list else 0.0
    speed_x = (total_duration_sec / elapsed_sec) if elapsed_sec > 0 else None

    scene_csv_path = video_output_dir / "scene_list.csv"
    with scene_csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "scene_index",
                "start_frame",
                "end_frame",
                "start_timecode",
                "end_timecode",
                "duration_frames",
                "duration_seconds",
            ],
        )
        writer.writeheader()
        for scene_index, (start, end) in enumerate(scene_list, start=1):
            writer.writerow(
                {
                    "scene_index": scene_index,
                    "start_frame": start.get_frames(),
                    "end_frame": end.get_frames(),
                    "start_timecode": start.get_timecode(),
                    "end_timecode": end.get_timecode(),
                    "duration_frames": end.get_frames() - start.get_frames(),
                    "duration_seconds": round(end.get_seconds() - start.get_seconds(), 3),
                }
            )

    cut_csv_path = video_output_dir / "cut_list.csv"
    with cut_csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["cut_index", "cut_frame", "cut_timecode", "cut_seconds"],
        )
        writer.writeheader()
        for cut_index, cut in enumerate(cut_list, start=1):
            writer.writerow(
                {
                    "cut_index": cut_index,
                    "cut_frame": cut.get_frames(),
                    "cut_timecode": cut.get_timecode(),
                    "cut_seconds": round(cut.get_seconds(), 3),
                }
            )

    summary = {
        "video_name": video_path.name,
        "video_path": str(video_path),
        "threshold": threshold,
        "min_scene_len_frames": min_scene_len,
        "num_scenes": len(scene_list),
        "num_cuts": len(cut_list),
        "total_duration_seconds": round(total_duration_sec, 3),
        "elapsed_seconds": round(elapsed_sec, 3),
        "processing_speed_x": round(speed_x, 3) if speed_x is not None else None,
        "cuts": [
            {
                "cut_index": cut_index,
                "frame": cut.get_frames(),
                "timecode": cut.get_timecode(),
                "seconds": round(cut.get_seconds(), 3),
            }
            for cut_index, cut in enumerate(cut_list, start=1)
        ],
        "output_dir": str(video_output_dir),
    }

    summary_path = video_output_dir / "summary.json"
    with summary_path.open("w", encoding="utf-8") as summary_file:
        json.dump(summary, summary_file, ensure_ascii=False, indent=2)
        summary_file.write("\n")

    return summary


def main() -> None:
    args = parse_args()
    videos = resolve_videos(args)
    if not videos:
        raise SystemExit("No videos found. Check --video, --input-dir, or --glob.")

    print(f"Input videos: {len(videos)}")
    print(f"Output dir: {args.output_dir.resolve()}")
    print(
        "Detector settings: "
        f"threshold={args.threshold}, min_scene_len={args.min_scene_len} frames"
    )

    summaries = []
    for video_path in videos:
        print(f"\nAnalyzing: {video_path.name}")
        summary = detect_scenes(
            video_path=video_path,
            output_dir=args.output_dir.resolve(),
            threshold=args.threshold,
            min_scene_len=args.min_scene_len,
        )
        summaries.append(summary)
        cut_timecodes = ", ".join(cut["timecode"] for cut in summary["cuts"]) or "none"
        speed_x = summary["processing_speed_x"]
        speed_str = f"{speed_x:.2f}x" if isinstance(speed_x, (int, float)) else "n/a"
        print(
            f"  scenes={summary['num_scenes']} "
            f"cuts={summary['num_cuts']} "
            f"runtime={summary['elapsed_seconds']}s "
            f"speed={speed_str}"
        )
        print(f"  cuts: {cut_timecodes}")

    aggregate_path = args.output_dir.resolve() / "run_summary.json"
    with aggregate_path.open("w", encoding="utf-8") as aggregate_file:
        json.dump(
            {
                "video_count": len(summaries),
                "threshold": args.threshold,
                "min_scene_len_frames": args.min_scene_len,
                "videos": summaries,
            },
            aggregate_file,
            ensure_ascii=False,
            indent=2,
        )
        aggregate_file.write("\n")

    print(f"\nSaved aggregate summary: {aggregate_path}")


if __name__ == "__main__":
    main()
