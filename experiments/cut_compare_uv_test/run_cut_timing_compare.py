from __future__ import annotations

import argparse
import datetime as dt
import html
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from run_cut_compare import (
    DEFAULT_ENV_FILE,
    DEFAULT_INPUT_DIR,
    DEFAULT_PROMPT_PATH,
    MethodResult,
    cut_boundaries,
    persist_method_result,
    read_video_metadata,
    run_gemini_cut_extraction,
    run_pyscenedetect,
    slugify,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "outputs" / "cut_timing_compare_uv_test"


@dataclass
class VideoTimingRow:
    video_name: str
    video_path: str
    duration_seconds: float
    pyscenedetect_cut_count: int
    gemini_cut_count: int
    pyscenedetect_elapsed_seconds: float
    gemini_elapsed_seconds: float
    gemini_pipeline_elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure PySceneDetect vs Gemini cut-extraction runtime once."
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--video", type=Path, help="Analyze a single video.")
    source_group.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing input videos.",
    )
    parser.add_argument("--glob", default="1[0-4]_*.mp4", help="Video glob for batch mode.")
    parser.add_argument("--limit", type=int, default=None, help="Optional video count cap.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for timing outputs.",
    )
    parser.add_argument(
        "--prompt-path",
        type=Path,
        default=DEFAULT_PROMPT_PATH,
        help="Prompt template for Gemini cut extraction.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="Optional .env file with GEMINI_API_KEY.",
    )
    parser.add_argument(
        "--gemini-model",
        default="gemini-2.5-pro",
        help="Gemini model name for cut extraction.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=3.5,
        help="PySceneDetect AdaptiveDetector adaptive_threshold.",
    )
    parser.add_argument(
        "--min-scene-len",
        type=int,
        default=30,
        help="PySceneDetect minimum scene length in frames.",
    )
    parser.add_argument(
        "--window-width",
        type=int,
        default=2,
        help="PySceneDetect AdaptiveDetector window width.",
    )
    parser.add_argument(
        "--min-content-val",
        type=float,
        default=15.0,
        help="PySceneDetect AdaptiveDetector minimum content value.",
    )
    return parser.parse_args()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


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


def record_elapsed_seconds(result: MethodResult, output_dir: Path, elapsed_seconds: float) -> None:
    result.metadata["processing_elapsed_seconds"] = round(elapsed_seconds, 3)
    persist_method_result(output_dir, result)


def measure_video(
    *,
    video_path: Path,
    run_dir: Path,
    args: argparse.Namespace,
) -> VideoTimingRow:
    metadata = read_video_metadata(video_path)
    video_dir = run_dir / slugify(video_path.stem)
    pyscene_dir = video_dir / "pyscenedetect"
    gemini_dir = video_dir / "gemini"
    pyscene_dir.mkdir(parents=True, exist_ok=True)
    gemini_dir.mkdir(parents=True, exist_ok=True)

    pyscene_started_at = perf_counter()
    pyscenedetect_result = run_pyscenedetect(
        video_path=video_path,
        metadata=metadata,
        output_dir=pyscene_dir,
        threshold=args.threshold,
        min_scene_len=args.min_scene_len,
        window_width=args.window_width,
        min_content_val=args.min_content_val,
    )
    pyscene_elapsed_seconds = perf_counter() - pyscene_started_at
    record_elapsed_seconds(pyscenedetect_result, pyscene_dir, pyscene_elapsed_seconds)

    scene_change_candidates = cut_boundaries(pyscenedetect_result.cuts)

    gemini_started_at = perf_counter()
    gemini_result = run_gemini_cut_extraction(
        video_path=video_path,
        metadata=metadata,
        scene_change_candidates=scene_change_candidates,
        output_dir=gemini_dir,
        prompt_path=args.prompt_path.resolve(),
        model=args.gemini_model,
        env_file=args.env_file.resolve(),
        dry_run=False,
    )
    gemini_elapsed_seconds = perf_counter() - gemini_started_at
    if gemini_result is None:
        raise RuntimeError(f"Gemini did not return a result for {video_path.name}.")
    record_elapsed_seconds(gemini_result, gemini_dir, gemini_elapsed_seconds)

    return VideoTimingRow(
        video_name=video_path.name,
        video_path=str(video_path),
        duration_seconds=round(metadata.duration_seconds, 3),
        pyscenedetect_cut_count=len(pyscenedetect_result.cuts),
        gemini_cut_count=len(gemini_result.cuts),
        pyscenedetect_elapsed_seconds=round(pyscene_elapsed_seconds, 3),
        gemini_elapsed_seconds=round(gemini_elapsed_seconds, 3),
        gemini_pipeline_elapsed_seconds=round(
            pyscene_elapsed_seconds + gemini_elapsed_seconds, 3
        ),
    )


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def build_table_rows(rows: list[VideoTimingRow]) -> str:
    rendered: list[str] = []
    for row in rows:
        gemini_only_ratio = (
            row.gemini_elapsed_seconds / row.pyscenedetect_elapsed_seconds
            if row.pyscenedetect_elapsed_seconds > 0
            else 0.0
        )
        gemini_pipeline_ratio = (
            row.gemini_pipeline_elapsed_seconds / row.pyscenedetect_elapsed_seconds
            if row.pyscenedetect_elapsed_seconds > 0
            else 0.0
        )
        rendered.append(
            "<tr>"
            f"<td>{html.escape(row.video_name)}</td>"
            f"<td>{row.duration_seconds:.3f}s</td>"
            f"<td>{row.pyscenedetect_cut_count}</td>"
            f"<td>{row.gemini_cut_count}</td>"
            f"<td>{row.pyscenedetect_elapsed_seconds:.3f}s</td>"
            f"<td>{row.gemini_elapsed_seconds:.3f}s</td>"
            f"<td>{row.gemini_pipeline_elapsed_seconds:.3f}s</td>"
            f"<td>{gemini_only_ratio:.2f}x</td>"
            f"<td>{gemini_pipeline_ratio:.2f}x</td>"
            "</tr>"
        )
    return "\n".join(rendered) if rendered else "<tr><td colspan='9'>No rows.</td></tr>"


def build_report_html(
    *,
    run_dir: Path,
    rows: list[VideoTimingRow],
    totals: dict[str, float],
    averages: dict[str, float],
) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cut Timing Comparison Report</title>
  <style>
    :root {{
      --ink: #1b1713;
      --muted: #6a625a;
      --paper: rgba(255, 250, 244, 0.94);
      --border: #d8ccc0;
      --accent: #8a4b2a;
      --mint: #dcefe7;
      --sand: #f4e5d8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, #f7dfbd 0%, transparent 30%),
        radial-gradient(circle at top right, #dcefe7 0%, transparent 34%),
        linear-gradient(180deg, #f5efe7 0%, #ece7df 100%);
    }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 28px 18px 80px; }}
    .panel {{
      background: var(--paper);
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 20px;
      margin-bottom: 22px;
      box-shadow: 0 14px 34px rgba(73, 49, 29, 0.08);
    }}
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
      margin-top: 16px;
    }}
    .metric-card {{
      background: var(--mint);
      border: 1px solid #c7ddd3;
      border-radius: 18px;
      padding: 14px;
    }}
    .metric-card.alt {{
      background: var(--sand);
      border-color: #e4d0bf;
    }}
    .metric-card strong {{
      display: block;
      font-size: 1.5rem;
      margin-bottom: 6px;
    }}
    p, td, th, li {{ line-height: 1.45; }}
    .muted {{ color: var(--muted); }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{
      padding: 11px 12px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }}
    th {{ background: rgba(138, 75, 42, 0.08); }}
    code {{ background: rgba(27, 23, 19, 0.06); padding: 1px 5px; border-radius: 6px; }}
  </style>
</head>
<body>
  <main>
    <section class="panel">
      <h1>Cut Timing Comparison Report</h1>
      <p class="muted">
        Measured one run across {len(rows)} videos. This report separates
        <code>PySceneDetect</code> cut extraction time from <code>Gemini</code> API and
        post-processing time. Because the current Gemini prompt consumes
        <code>scene_change_candidates</code>, a pipeline total including PySceneDetect
        candidate generation is also shown.
      </p>
      <div class="metric-grid">
        <div class="metric-card">
          <strong>{totals["pyscenedetect_elapsed_seconds"]:.3f}s</strong>
          <span>Total PySceneDetect time</span>
        </div>
        <div class="metric-card">
          <strong>{totals["gemini_elapsed_seconds"]:.3f}s</strong>
          <span>Total Gemini-only time</span>
        </div>
        <div class="metric-card alt">
          <strong>{totals["gemini_pipeline_elapsed_seconds"]:.3f}s</strong>
          <span>Total Gemini pipeline time</span>
        </div>
        <div class="metric-card">
          <strong>{averages["pyscenedetect_elapsed_seconds"]:.3f}s</strong>
          <span>Avg PySceneDetect / video</span>
        </div>
        <div class="metric-card">
          <strong>{averages["gemini_elapsed_seconds"]:.3f}s</strong>
          <span>Avg Gemini-only / video</span>
        </div>
        <div class="metric-card alt">
          <strong>{averages["gemini_pipeline_elapsed_seconds"]:.3f}s</strong>
          <span>Avg Gemini pipeline / video</span>
        </div>
      </div>
    </section>

    <section class="panel">
      <h2>Per-video Runtime</h2>
      <table>
        <thead>
          <tr>
            <th>Video</th>
            <th>Duration</th>
            <th>PySceneDetect Cuts</th>
            <th>Gemini Cuts</th>
            <th>PySceneDetect</th>
            <th>Gemini Only</th>
            <th>Gemini Pipeline</th>
            <th>Gemini Only / PySceneDetect</th>
            <th>Gemini Pipeline / PySceneDetect</th>
          </tr>
        </thead>
        <tbody>
          {build_table_rows(rows)}
        </tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    videos = resolve_videos(args)
    if not videos:
        raise SystemExit("No videos found to analyze.")

    run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = args.output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    rows: list[VideoTimingRow] = []
    for video_path in videos:
        print(f"Measuring PySceneDetect and Gemini for {video_path.name}")
        rows.append(
            measure_video(
                video_path=video_path,
                run_dir=run_dir,
                args=args,
            )
        )

    totals = {
        "pyscenedetect_elapsed_seconds": round(
            sum(row.pyscenedetect_elapsed_seconds for row in rows), 3
        ),
        "gemini_elapsed_seconds": round(sum(row.gemini_elapsed_seconds for row in rows), 3),
        "gemini_pipeline_elapsed_seconds": round(
            sum(row.gemini_pipeline_elapsed_seconds for row in rows), 3
        ),
    }
    averages = {
        "pyscenedetect_elapsed_seconds": round(
            average([row.pyscenedetect_elapsed_seconds for row in rows]), 3
        ),
        "gemini_elapsed_seconds": round(
            average([row.gemini_elapsed_seconds for row in rows]), 3
        ),
        "gemini_pipeline_elapsed_seconds": round(
            average([row.gemini_pipeline_elapsed_seconds for row in rows]), 3
        ),
    }

    write_json(
        run_dir / "timing_summary.json",
        {
            "run_id": run_id,
            "video_count": len(rows),
            "videos": [row.to_dict() for row in rows],
            "totals": totals,
            "averages": averages,
            "notes": {
                "gemini_only_elapsed_seconds": (
                    "Gemini API call + response parsing/normalization only."
                ),
                "gemini_pipeline_elapsed_seconds": (
                    "Current implementation total: PySceneDetect candidate generation + Gemini-only."
                ),
            },
        },
    )
    write_text(
        run_dir / "timing_report.html",
        build_report_html(
            run_dir=run_dir,
            rows=rows,
            totals=totals,
            averages=averages,
        ),
    )
    print(f"Saved timing comparison artifacts to {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
