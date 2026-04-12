from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from run_cut_compare import (
    DEFAULT_ENV_FILE,
    DEFAULT_INPUT_DIR,
    DEFAULT_PROMPT_PATH,
    PairwiseVideoResult,
    average,
    build_pairwise_video_section_html,
    load_pairwise_video_results,
    relative_path,
)


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "outputs" / "adaptive_threshold_sweep_uv_test"
RUN_COMPARE_SCRIPT = SCRIPT_DIR / "run_cut_compare.py"


@dataclass
class ThresholdRun:
    threshold: float
    threshold_label: str
    run_dir: str
    comparison_rows: list[dict[str, Any]]
    pairwise_video_results: list[PairwiseVideoResult]
    pairwise_report_path: str
    pairwise_differing_report_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "threshold_label": self.threshold_label,
            "run_dir": self.run_dir,
            "pairwise_report_path": self.pairwise_report_path,
            "pairwise_differing_report_path": self.pairwise_differing_report_path,
            "comparison_rows": self.comparison_rows,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run AdaptiveDetector threshold sweep and build pairwise HTML report."
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
        "--thresholds",
        default="3.5,4.0,5.0",
        help="Comma-separated AdaptiveDetector thresholds in increasing order.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for threshold sweep outputs.",
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
        "--min-scene-len",
        type=int,
        default=30,
        help="AdaptiveDetector minimum scene length in frames.",
    )
    parser.add_argument(
        "--window-width",
        type=int,
        default=2,
        help="AdaptiveDetector window width.",
    )
    parser.add_argument(
        "--min-content-val",
        type=float,
        default=15.0,
        help="AdaptiveDetector minimum content value.",
    )
    parser.add_argument(
        "--boundary-tolerance",
        type=float,
        default=0.5,
        help="Boundary matching tolerance in seconds.",
    )
    return parser.parse_args()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def parse_thresholds(raw: str) -> list[float]:
    values = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(float(item))
    if not values:
        raise SystemExit("No thresholds were provided.")
    return values


def format_threshold_label(threshold: float) -> str:
    return f"{threshold:.1f}".rstrip("0").rstrip(".")


def determine_trend(counts: list[int]) -> str:
    if all(value == counts[0] for value in counts):
        return "flat"
    if all(left >= right for left, right in zip(counts, counts[1:])):
        return "down"
    if all(left <= right for left, right in zip(counts, counts[1:])):
        return "up"
    return "mixed"


def run_threshold(
    *,
    session_dir: Path,
    threshold: float,
    args: argparse.Namespace,
) -> ThresholdRun:
    threshold_label = format_threshold_label(threshold)
    threshold_dir = session_dir / f"threshold_{threshold_label.replace('.', '_')}"
    child_output_root = threshold_dir / "outputs"
    child_output_root.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        str(RUN_COMPARE_SCRIPT),
        "--threshold",
        str(threshold),
        "--min-scene-len",
        str(args.min_scene_len),
        "--window-width",
        str(args.window_width),
        "--min-content-val",
        str(args.min_content_val),
        "--boundary-tolerance",
        str(args.boundary_tolerance),
        "--gemini-model",
        args.gemini_model,
        "--prompt-path",
        str(args.prompt_path.resolve()),
        "--env-file",
        str(args.env_file.resolve()),
        "--output-root",
        str(child_output_root),
    ]
    if args.video is not None:
        command.extend(["--video", str(args.video.resolve())])
    else:
        command.extend(["--input-dir", str(args.input_dir.resolve()), "--glob", args.glob])
        if args.limit is not None:
            command.extend(["--limit", str(args.limit)])

    print(f"[threshold {threshold_label}] starting run")
    completed = subprocess.run(
        command,
        cwd=SCRIPT_DIR,
        text=True,
        capture_output=True,
        check=False,
    )
    write_text(threshold_dir / "stdout.log", completed.stdout)
    write_text(threshold_dir / "stderr.log", completed.stderr)
    if completed.returncode != 0:
        raise RuntimeError(
            f"Threshold {threshold_label} run failed. See {threshold_dir / 'stderr.log'}."
        )

    run_dirs = sorted(path for path in child_output_root.iterdir() if path.is_dir())
    if not run_dirs:
        raise RuntimeError(f"Threshold {threshold_label} produced no run directory.")
    run_dir = run_dirs[-1]
    pairwise_video_results, comparison_rows, _ = load_pairwise_video_results(run_dir)
    print(f"[threshold {threshold_label}] completed -> {run_dir}")
    return ThresholdRun(
        threshold=threshold,
        threshold_label=threshold_label,
        run_dir=str(run_dir),
        comparison_rows=comparison_rows,
        pairwise_video_results=pairwise_video_results,
        pairwise_report_path=str(run_dir / "pairwise_comparison_report.html"),
        pairwise_differing_report_path=str(run_dir / "pairwise_differing_report.html"),
    )


def build_threshold_summary_rows(session_dir: Path, threshold_runs: list[ThresholdRun]) -> str:
    rows = []
    for run in threshold_runs:
        differing_count = sum(1 for row in run.comparison_rows if row["differing"])
        exact_count = len(run.comparison_rows) - differing_count
        average_pyscene_cuts = average(
            [float(row["pyscenedetect_cut_count"]) for row in run.comparison_rows]
        )
        average_gemini_cuts = average(
            [float(row["gemini_cut_count"]) for row in run.comparison_rows]
        )
        average_f1 = average([float(row["f1_score"]) for row in run.comparison_rows])
        pairwise_rel = relative_path(session_dir, Path(run.pairwise_report_path))
        differing_rel = relative_path(session_dir, Path(run.pairwise_differing_report_path))
        rows.append(
            "<tr>"
            f"<td>{run.threshold_label}</td>"
            f"<td>{average_pyscene_cuts:.2f}</td>"
            f"<td>{average_gemini_cuts:.2f}</td>"
            f"<td>{average_f1:.3f}</td>"
            f"<td>{exact_count}</td>"
            f"<td>{differing_count}</td>"
            f"<td><a href='{html.escape(pairwise_rel)}'>pairwise report</a></td>"
            f"<td><a href='{html.escape(differing_rel)}'>differing only</a></td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_video_cut_count_rows(threshold_runs: list[ThresholdRun]) -> str:
    by_video: dict[str, dict[str, list[int]]] = {}
    threshold_labels = [run.threshold_label for run in threshold_runs]
    for run in threshold_runs:
        for row in run.comparison_rows:
            entry = by_video.setdefault(
                row["video_name"],
                {"pyscenedetect": [], "gemini": []},
            )
            entry["pyscenedetect"].append(int(row["pyscenedetect_cut_count"]))
            entry["gemini"].append(int(row["gemini_cut_count"]))

    rows = []
    for video_name, methods in sorted(by_video.items()):
        pyscene_counts = methods["pyscenedetect"]
        gemini_counts = methods["gemini"]
        rows.append(
            "<tr>"
            f"<td>{html.escape(video_name)}</td>"
            f"<td>{html.escape(', '.join(f'{label}:{count}' for label, count in zip(threshold_labels, pyscene_counts)))}</td>"
            f"<td>{determine_trend(pyscene_counts)}</td>"
            f"<td>{html.escape(', '.join(f'{label}:{count}' for label, count in zip(threshold_labels, gemini_counts)))}</td>"
            f"<td>{determine_trend(gemini_counts)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_threshold_nav(threshold_runs: list[ThresholdRun]) -> str:
    cards = []
    for run in threshold_runs:
        differing_count = sum(1 for row in run.comparison_rows if row["differing"])
        average_pyscene_cuts = average(
            [float(row["pyscenedetect_cut_count"]) for row in run.comparison_rows]
        )
        cards.append(
            "<a class='nav-card' href='#threshold-"
            + html.escape(run.threshold_label.replace(".", "-"))
            + "'>"
            + f"<strong>threshold {run.threshold_label}</strong>"
            + f"<span class='nav-sub'>avg PySceneDetect cuts {average_pyscene_cuts:.2f}</span>"
            + f"<span class='nav-sub'>differing videos {differing_count}/{len(run.comparison_rows)}</span>"
            + "</a>"
        )
    return "\n".join(cards)


def build_threshold_sections(session_dir: Path, threshold_runs: list[ThresholdRun]) -> str:
    sections = []
    for run in threshold_runs:
        section_id = f"threshold-{run.threshold_label.replace('.', '-')}"
        modified_results = [
            replace(
                bundle,
                video_slug=f"{section_id}-{bundle.video_slug}",
            )
            for bundle in run.pairwise_video_results
        ]
        pairwise_rel = relative_path(session_dir, Path(run.pairwise_report_path))
        differing_rel = relative_path(session_dir, Path(run.pairwise_differing_report_path))
        average_pyscene_cuts = average(
            [float(row["pyscenedetect_cut_count"]) for row in run.comparison_rows]
        )
        average_gemini_cuts = average(
            [float(row["gemini_cut_count"]) for row in run.comparison_rows]
        )
        threshold_sections = "\n".join(
            build_pairwise_video_section_html(page_dir=session_dir, bundle=bundle)
            for bundle in modified_results
        )
        sections.append(
            f"""
    <section id="{html.escape(section_id)}" class="panel threshold-panel">
      <div class="threshold-head">
        <div>
          <h2>Adaptive threshold {html.escape(run.threshold_label)}</h2>
          <p class="muted">AdaptiveDetector | min_scene_len={run.pairwise_video_results[0].pyscenedetect_result.parameters.get('min_scene_len_frames')} frames | window_width={run.pairwise_video_results[0].pyscenedetect_result.parameters.get('window_width')} | min_content_val={run.pairwise_video_results[0].pyscenedetect_result.parameters.get('min_content_val')}</p>
        </div>
        <div class="threshold-links">
          <a href="{html.escape(pairwise_rel)}">threshold report</a>
          <a href="{html.escape(differing_rel)}">differing only</a>
        </div>
      </div>
      <div class="metric-row">
        <div class="metric-card"><strong>{average_pyscene_cuts:.2f}</strong><span>avg PySceneDetect cuts</span></div>
        <div class="metric-card"><strong>{average_gemini_cuts:.2f}</strong><span>avg Gemini cuts</span></div>
        <div class="metric-card"><strong>{sum(1 for row in run.comparison_rows if row['differing'])}</strong><span>differing videos</span></div>
        <div class="metric-card"><strong>{average([float(row['f1_score']) for row in run.comparison_rows]):.3f}</strong><span>avg boundary F1</span></div>
      </div>
      {threshold_sections}
    </section>
            """.strip()
        )
    return "\n\n".join(sections)


def build_report_html(
    *,
    session_dir: Path,
    threshold_runs: list[ThresholdRun],
) -> str:
    threshold_values = ", ".join(run.threshold_label for run in threshold_runs)
    first_run = threshold_runs[0]
    first_params = first_run.pairwise_video_results[0].pyscenedetect_result.parameters
    average_pyscene_cuts = [
        average([float(row["pyscenedetect_cut_count"]) for row in run.comparison_rows])
        for run in threshold_runs
    ]
    threshold_effect = determine_trend([int(round(value * 1000)) for value in average_pyscene_cuts])
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Adaptive Threshold Sweep Report</title>
  <style>
    :root {{
      --ink: #1a1613;
      --muted: #675f58;
      --paper: rgba(255, 250, 245, 0.94);
      --panel: rgba(255, 255, 255, 0.94);
      --border: #d9cfc5;
      --accent: #8a4b2a;
      --accent-soft: #f3e4d7;
      --mint: #dcefe7;
      --match: #166534;
      --diff: #b91c1c;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, #f9e1bd 0%, transparent 30%),
        radial-gradient(circle at top right, #dcefe7 0%, transparent 35%),
        linear-gradient(180deg, #f5efe7 0%, #ece8e0 100%);
    }}
    main {{ max-width: 1560px; margin: 0 auto; padding: 28px 18px 80px; }}
    .panel {{
      background: var(--paper);
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 20px;
      margin-bottom: 22px;
      box-shadow: 0 14px 34px rgba(73, 49, 29, 0.08);
      backdrop-filter: blur(6px);
    }}
    h1, h2, h3, h4 {{ margin: 0 0 12px; }}
    p, li, td, th, summary, span {{ line-height: 1.45; }}
    a {{ color: var(--accent); }}
    .summary {{ color: var(--muted); max-width: 1080px; }}
    .nav-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 14px;
      margin-top: 18px;
    }}
    .nav-card {{
      display: flex;
      flex-direction: column;
      gap: 8px;
      text-decoration: none;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 14px;
    }}
    .nav-sub {{ color: var(--muted); font-size: 0.95rem; }}
    .threshold-panel {{ scroll-margin-top: 20px; }}
    .threshold-head {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: start;
      margin-bottom: 16px;
    }}
    .threshold-links {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .threshold-links a {{
      text-decoration: none;
      padding: 8px 12px;
      border-radius: 999px;
      background: var(--accent-soft);
      border: 1px solid #e3cdbb;
    }}
    .muted {{ color: var(--muted); }}
    .metric-row {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px;
      margin-bottom: 16px;
    }}
    .metric-card {{
      background: var(--mint);
      border: 1px solid #c7ddd3;
      border-radius: 18px;
      padding: 14px;
    }}
    .metric-card strong {{ display: block; font-size: 1.5rem; margin-bottom: 6px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }}
    th {{ background: rgba(138, 75, 42, 0.08); }}
    .video-section {{ scroll-margin-top: 20px; }}
    .video-head {{
      display: flex;
      justify-content: space-between;
      align-items: start;
      gap: 16px;
      margin-bottom: 16px;
    }}
    .badge {{
      display: inline-block;
      padding: 5px 10px;
      border-radius: 999px;
      color: #fff;
      font-size: 0.9rem;
      font-weight: 700;
      white-space: nowrap;
    }}
    .badge.match {{ background: var(--match); }}
    .badge.diff {{ background: var(--diff); }}
    .boundary-box {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 16px;
      margin-bottom: 18px;
    }}
    .pair-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
      align-items: start;
    }}
    .method-panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 16px;
    }}
    .clip-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-bottom: 12px;
    }}
    .clip-card {{
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 10px;
    }}
    .clip-card h4 {{ font-size: 0.98rem; }}
    .clip-card video {{
      width: 100%;
      border-radius: 10px;
      background: #000;
    }}
    details[open] summary {{ margin-bottom: 10px; }}
    .warning-list {{ margin: 12px 0 0; padding-left: 18px; color: #92400e; }}
    @media (max-width: 980px) {{
      .pair-grid {{ grid-template-columns: 1fr; }}
      .threshold-head {{ flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="panel">
      <h1>Adaptive Threshold Sweep Report</h1>
      <p class="summary">
        Compared 5 videos with thresholds {html.escape(threshold_values)} using
        AdaptiveDetector. This report shows threshold-specific pairwise clip views
        for PySceneDetect vs Gemini, plus cut-count statistics so you can inspect
        whether raising the threshold actually reduces segmentation density.
      </p>
      <p class="summary">
        Fixed params: min_scene_len={first_params.get('min_scene_len_frames')} frames,
        window_width={first_params.get('window_width')},
        min_content_val={first_params.get('min_content_val')},
        boundary_tolerance={first_run.comparison_rows[0]['tolerance_seconds']:.1f}s.
        Overall average PySceneDetect cut trend across thresholds: <strong>{html.escape(threshold_effect)}</strong>.
      </p>
      <div class="nav-grid">
        {build_threshold_nav(threshold_runs)}
      </div>
    </section>

    <section class="panel">
      <h2>Threshold Summary</h2>
      <table>
        <thead>
          <tr>
            <th>Adaptive Threshold</th>
            <th>Avg PySceneDetect Cuts</th>
            <th>Avg Gemini Cuts</th>
            <th>Avg Boundary F1</th>
            <th>Exact Matches</th>
            <th>Differing Videos</th>
            <th>Pairwise</th>
            <th>Differing Only</th>
          </tr>
        </thead>
        <tbody>
          {build_threshold_summary_rows(session_dir, threshold_runs)}
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h2>Cut Count Effect By Video</h2>
      <table>
        <thead>
          <tr>
            <th>Video</th>
            <th>PySceneDetect Counts</th>
            <th>PySceneDetect Trend</th>
            <th>Gemini Counts</th>
            <th>Gemini Trend</th>
          </tr>
        </thead>
        <tbody>
          {build_video_cut_count_rows(threshold_runs)}
        </tbody>
      </table>
    </section>

    {build_threshold_sections(session_dir, threshold_runs)}
  </main>
</body>
</html>
"""


def build_summary_payload(
    *,
    session_id: str,
    threshold_runs: list[ThresholdRun],
) -> dict[str, Any]:
    threshold_stats = []
    for run in threshold_runs:
        threshold_stats.append(
            {
                "threshold": run.threshold,
                "threshold_label": run.threshold_label,
                "compared_video_count": len(run.comparison_rows),
                "differing_video_count": sum(
                    1 for row in run.comparison_rows if row["differing"]
                ),
                "exact_match_video_count": sum(
                    1 for row in run.comparison_rows if not row["differing"]
                ),
                "average_pyscenedetect_cut_count": round(
                    average(
                        [float(row["pyscenedetect_cut_count"]) for row in run.comparison_rows]
                    ),
                    4,
                ),
                "average_gemini_cut_count": round(
                    average([float(row["gemini_cut_count"]) for row in run.comparison_rows]),
                    4,
                ),
                "average_f1_score": round(
                    average([float(row["f1_score"]) for row in run.comparison_rows]),
                    4,
                ),
                "run_dir": run.run_dir,
                "pairwise_report_path": run.pairwise_report_path,
                "pairwise_differing_report_path": run.pairwise_differing_report_path,
            }
        )
    return {
        "session_id": session_id,
        "threshold_runs": [run.to_dict() for run in threshold_runs],
        "threshold_stats": threshold_stats,
    }


def main() -> int:
    args = parse_args()
    thresholds = parse_thresholds(args.thresholds)
    session_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = args.output_root.resolve() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    threshold_runs = [
        run_threshold(session_dir=session_dir, threshold=threshold, args=args)
        for threshold in thresholds
    ]

    write_json(
        session_dir / "threshold_sweep_summary.json",
        build_summary_payload(session_id=session_id, threshold_runs=threshold_runs),
    )
    write_text(
        session_dir / "threshold_sweep_pairwise_report.html",
        build_report_html(session_dir=session_dir, threshold_runs=threshold_runs),
    )
    print(f"Saved threshold sweep artifacts to {session_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
