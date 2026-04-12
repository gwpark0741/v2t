from __future__ import annotations

import argparse
import datetime as dt
import html
import itertools
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from run_cut_compare import (
    DEFAULT_ENV_FILE,
    DEFAULT_INPUT_DIR,
    DEFAULT_PROMPT_PATH,
    CutSegment,
    MethodResult,
    average,
    build_clip_cards,
    build_cut_rows,
    build_value_list,
    cut_boundaries,
    load_json,
    method_result_from_dict,
    relative_path,
    slugify,
)


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "outputs" / "cut_compare_stability_uv_test"
RUN_COMPARE_SCRIPT = SCRIPT_DIR / "run_cut_compare.py"
MAX_REPEAT_ATTEMPTS = 2


@dataclass
class RepeatRun:
    repeat_index: int
    repeat_root: str
    run_dir: str
    elapsed_seconds: float
    pairwise_report_path: str
    pairwise_differing_report_path: str
    comparison_report_path: str
    differing_report_path: str


@dataclass
class PairwiseAgreement:
    left_run: int
    right_run: int
    left_cut_count: int
    right_cut_count: int
    left_boundary_count: int
    right_boundary_count: int
    matched_boundary_count: int
    precision: float
    recall: float
    f1_score: float
    exact_match: bool
    unmatched_left_boundaries: list[float]
    unmatched_right_boundaries: list[float]
    matched_boundaries: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MethodStability:
    method: str
    video_name: str
    run_cut_counts: list[int]
    run_boundary_counts: list[int]
    pairwise_agreements: list[PairwiseAgreement]
    average_pairwise_f1: float
    minimum_pairwise_f1: float
    exact_match_pair_count: int
    unstable: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["pairwise_agreements"] = [pair.to_dict() for pair in self.pairwise_agreements]
        return payload


@dataclass
class VideoStability:
    video_name: str
    video_slug: str
    methods: dict[str, MethodStability]

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_name": self.video_name,
            "video_slug": self.video_slug,
            "methods": {name: value.to_dict() for name, value in self.methods.items()},
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run repeated cut comparison experiments and measure stability."
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
        "--repeat-count",
        type=int,
        default=3,
        help="Number of repeated runs to execute.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for stability experiment outputs.",
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
        default=27.0,
        help="PySceneDetect ContentDetector threshold.",
    )
    parser.add_argument(
        "--min-scene-len",
        type=int,
        default=10,
        help="PySceneDetect minimum scene length in frames.",
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


def select_videos(args: argparse.Namespace) -> list[Path]:
    if args.video is not None:
        return [args.video.resolve()]

    input_dir = args.input_dir.resolve()
    if not input_dir.exists():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    videos = sorted(path.resolve() for path in input_dir.glob(args.glob) if path.is_file())
    if args.limit is not None:
        videos = videos[: args.limit]
    return videos


def run_repeat(
    *,
    repeat_index: int,
    session_dir: Path,
    videos: list[Path],
    args: argparse.Namespace,
) -> RepeatRun:
    repeat_root = session_dir / f"repeat_{repeat_index:02d}"
    command_base = [
        sys.executable,
        str(RUN_COMPARE_SCRIPT),
        "--threshold",
        str(args.threshold),
        "--min-scene-len",
        str(args.min_scene_len),
        "--boundary-tolerance",
        str(args.boundary_tolerance),
        "--gemini-model",
        args.gemini_model,
        "--prompt-path",
        str(args.prompt_path.resolve()),
        "--env-file",
        str(args.env_file.resolve()),
    ]
    if args.video is not None:
        command_base.extend(["--video", str(videos[0])])
    else:
        command_base.extend(["--input-dir", str(args.input_dir.resolve()), "--glob", args.glob])
        if args.limit is not None:
            command_base.extend(["--limit", str(args.limit)])

    print(f"[repeat {repeat_index}] starting full pairwise run for {len(videos)} videos")
    last_error: str | None = None
    for attempt_index in range(1, MAX_REPEAT_ATTEMPTS + 1):
        attempt_root = repeat_root / f"attempt_{attempt_index:02d}"
        child_output_root = attempt_root / "outputs"
        child_output_root.mkdir(parents=True, exist_ok=True)
        command = [
            *command_base,
            "--output-root",
            str(child_output_root),
        ]

        started_at = perf_counter()
        completed = subprocess.run(
            command,
            cwd=SCRIPT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        elapsed_seconds = perf_counter() - started_at

        write_text(attempt_root / "stdout.log", completed.stdout)
        write_text(attempt_root / "stderr.log", completed.stderr)

        if completed.returncode == 0:
            run_dirs = sorted(path for path in child_output_root.iterdir() if path.is_dir())
            if not run_dirs:
                raise RuntimeError(
                    f"Repeat {repeat_index} attempt {attempt_index} produced no run directory."
                )
            run_dir = run_dirs[-1]
            print(
                f"[repeat {repeat_index}] completed in {elapsed_seconds:.1f}s "
                f"(attempt {attempt_index}) -> {run_dir}"
            )
            return RepeatRun(
                repeat_index=repeat_index,
                repeat_root=str(repeat_root),
                run_dir=str(run_dir),
                elapsed_seconds=round(elapsed_seconds, 3),
                pairwise_report_path=str(run_dir / "pairwise_comparison_report.html"),
                pairwise_differing_report_path=str(run_dir / "pairwise_differing_report.html"),
                comparison_report_path=str(run_dir / "comparison_report.html"),
                differing_report_path=str(run_dir / "differing_videos_report.html"),
            )

        last_error = (
            f"attempt {attempt_index} failed with exit code {completed.returncode}. "
            f"See {attempt_root / 'stderr.log'}."
        )
        print(
            f"[repeat {repeat_index}] attempt {attempt_index} failed; "
            f"retrying." if attempt_index < MAX_REPEAT_ATTEMPTS else
            f"[repeat {repeat_index}] attempt {attempt_index} failed; no retries left."
        )

    raise RuntimeError(f"Repeat {repeat_index} failed after retries. {last_error}")


def load_repeat_video_method_results(run_dir: Path) -> dict[str, dict[str, MethodResult]]:
    comparison_summary = load_json(run_dir / "comparison_summary.json")
    rows = comparison_summary.get("videos", [])
    loaded: dict[str, dict[str, MethodResult]] = {}
    for row in rows:
        video_name = row["video_name"]
        video_slug = slugify(Path(video_name).stem)
        video_dir = run_dir / video_slug
        loaded[video_name] = {
            "pyscenedetect": method_result_from_dict(
                load_json(video_dir / "pyscenedetect" / "result.json")
            ),
            "gemini": method_result_from_dict(load_json(video_dir / "gemini" / "result.json")),
        }
    return loaded


def compare_method_runs(
    *,
    left_run: int,
    right_run: int,
    left_result: MethodResult,
    right_result: MethodResult,
    tolerance_seconds: float,
) -> PairwiseAgreement:
    left_boundaries = cut_boundaries(left_result.cuts)
    right_boundaries = cut_boundaries(right_result.cuts)

    matched_boundaries: list[dict[str, Any]] = []
    unmatched_left: list[float] = []
    unmatched_right: list[float] = []

    left_index = 0
    right_index = 0
    while left_index < len(left_boundaries) and right_index < len(right_boundaries):
        left_time = left_boundaries[left_index]
        right_time = right_boundaries[right_index]
        delta = round(right_time - left_time, 3)

        if abs(delta) <= tolerance_seconds:
            matched_boundaries.append(
                {
                    "left_boundary": left_time,
                    "right_boundary": right_time,
                    "delta_seconds": delta,
                }
            )
            left_index += 1
            right_index += 1
        elif left_time < right_time:
            unmatched_left.append(left_time)
            left_index += 1
        else:
            unmatched_right.append(right_time)
            right_index += 1

    unmatched_left.extend(left_boundaries[left_index:])
    unmatched_right.extend(right_boundaries[right_index:])

    matched_count = len(matched_boundaries)
    precision = matched_count / len(right_boundaries) if right_boundaries else 1.0
    recall = matched_count / len(left_boundaries) if left_boundaries else 1.0
    f1_score = (
        0.0 if precision + recall == 0.0 else (2 * precision * recall) / (precision + recall)
    )

    return PairwiseAgreement(
        left_run=left_run,
        right_run=right_run,
        left_cut_count=len(left_result.cuts),
        right_cut_count=len(right_result.cuts),
        left_boundary_count=len(left_boundaries),
        right_boundary_count=len(right_boundaries),
        matched_boundary_count=matched_count,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1_score=round(f1_score, 4),
        exact_match=not unmatched_left and not unmatched_right,
        unmatched_left_boundaries=unmatched_left,
        unmatched_right_boundaries=unmatched_right,
        matched_boundaries=matched_boundaries,
    )


def build_method_stability(
    *,
    method: str,
    video_name: str,
    results_by_run: list[MethodResult],
    tolerance_seconds: float,
) -> MethodStability:
    pairwise_agreements: list[PairwiseAgreement] = []
    for (left_index, left_result), (right_index, right_result) in itertools.combinations(
        list(enumerate(results_by_run, start=1)), 2
    ):
        pairwise_agreements.append(
            compare_method_runs(
                left_run=left_index,
                right_run=right_index,
                left_result=left_result,
                right_result=right_result,
                tolerance_seconds=tolerance_seconds,
            )
        )

    pairwise_f1s = [pair.f1_score for pair in pairwise_agreements]
    exact_matches = sum(1 for pair in pairwise_agreements if pair.exact_match)
    return MethodStability(
        method=method,
        video_name=video_name,
        run_cut_counts=[len(result.cuts) for result in results_by_run],
        run_boundary_counts=[len(cut_boundaries(result.cuts)) for result in results_by_run],
        pairwise_agreements=pairwise_agreements,
        average_pairwise_f1=round(average(pairwise_f1s), 4),
        minimum_pairwise_f1=min(pairwise_f1s) if pairwise_f1s else 1.0,
        exact_match_pair_count=exact_matches,
        unstable=exact_matches != len(pairwise_agreements),
    )


def build_video_stabilities(
    *,
    repeat_runs: list[RepeatRun],
    tolerance_seconds: float,
) -> list[VideoStability]:
    loaded_runs = [
        load_repeat_video_method_results(Path(repeat.run_dir)) for repeat in repeat_runs
    ]
    if not loaded_runs:
        return []

    video_names = sorted(loaded_runs[0].keys())
    videos: list[VideoStability] = []
    for video_name in video_names:
        methods: dict[str, MethodStability] = {}
        for method in ("pyscenedetect", "gemini"):
            results_by_run = [loaded[video_name][method] for loaded in loaded_runs]
            methods[method] = build_method_stability(
                method=method,
                video_name=video_name,
                results_by_run=results_by_run,
                tolerance_seconds=tolerance_seconds,
            )
        videos.append(
            VideoStability(
                video_name=video_name,
                video_slug=slugify(Path(video_name).stem),
                methods=methods,
            )
        )
    return videos


def build_pairwise_rows(agreements: list[PairwiseAgreement]) -> str:
    rows = []
    for pair in agreements:
        rows.append(
            "<tr>"
            f"<td>R{pair.left_run} vs R{pair.right_run}</td>"
            f"<td>{pair.left_cut_count} vs {pair.right_cut_count}</td>"
            f"<td>{pair.matched_boundary_count}</td>"
            f"<td>{pair.precision:.3f}</td>"
            f"<td>{pair.recall:.3f}</td>"
            f"<td>{pair.f1_score:.3f}</td>"
            f"<td>{'MATCH' if pair.exact_match else 'DIFF'}</td>"
            f"<td>{html.escape(build_value_list(pair.unmatched_left_boundaries))}</td>"
            f"<td>{html.escape(build_value_list(pair.unmatched_right_boundaries))}</td>"
            "</tr>"
        )
    return "\n".join(rows) if rows else "<tr><td colspan='9'>No pairwise rows.</td></tr>"


def build_run_cards(
    *,
    page_dir: Path,
    repeat_runs: list[RepeatRun],
    method_results_by_run: list[MethodResult],
) -> str:
    cards = []
    for repeat, method_result in zip(repeat_runs, method_results_by_run):
        run_dir = Path(repeat.run_dir)
        video_slug = slugify(Path(method_result.video_name).stem)
        report_rel = relative_path(page_dir, run_dir / video_slug / "report.html")
        cards.append(
            "<div class='run-card'>"
            f"<h4>Run {repeat.repeat_index}</h4>"
            f"<p class='muted'>cuts={len(method_result.cuts)}, boundaries={len(cut_boundaries(method_result.cuts))}, elapsed={repeat.elapsed_seconds:.1f}s (full run)</p>"
            f"<p><a href='{html.escape(report_rel)}'>open run report</a></p>"
            "<details>"
            "<summary>Clips and cut table</summary>"
            f"<div class='clip-grid'>{build_clip_cards(page_dir=page_dir, clips=method_result.clips)}</div>"
            "<table>"
            "<thead><tr><th>Cut ID</th><th>Start</th><th>End</th><th>Duration</th><th>Camera Angle</th><th>Transition In</th><th>Transition Out</th><th>Notes</th></tr></thead>"
            f"<tbody>{build_cut_rows(method_result.cuts)}</tbody>"
            "</table>"
            "</details>"
            "</div>"
        )
    return "\n".join(cards)


def build_method_panel(
    *,
    page_dir: Path,
    repeat_runs: list[RepeatRun],
    method_stability: MethodStability,
    method_results_by_run: list[MethodResult],
) -> str:
    stable_badge = "STABLE" if not method_stability.unstable else "UNSTABLE"
    badge_class = "match" if not method_stability.unstable else "diff"
    return f"""
    <div class="method-panel">
      <div class="method-head">
        <h3>{html.escape(method_stability.method)}</h3>
        <span class="badge {badge_class}">{stable_badge}</span>
      </div>
      <div class="metric-row">
        <div class="metric-card"><strong>{method_stability.average_pairwise_f1:.3f}</strong><span>avg pairwise F1</span></div>
        <div class="metric-card"><strong>{method_stability.minimum_pairwise_f1:.3f}</strong><span>min pairwise F1</span></div>
        <div class="metric-card"><strong>{method_stability.exact_match_pair_count}/{len(method_stability.pairwise_agreements)}</strong><span>exact-match pairs</span></div>
        <div class="metric-card"><strong>{html.escape(str(method_stability.run_cut_counts))}</strong><span>cut counts by run</span></div>
      </div>
      <details open>
        <summary>Pairwise stability table</summary>
        <table>
          <thead>
            <tr>
              <th>Pair</th>
              <th>Cut Counts</th>
              <th>Matched Boundaries</th>
              <th>Precision</th>
              <th>Recall</th>
              <th>F1</th>
              <th>Status</th>
              <th>Unmatched Left</th>
              <th>Unmatched Right</th>
            </tr>
          </thead>
          <tbody>
            {build_pairwise_rows(method_stability.pairwise_agreements)}
          </tbody>
        </table>
      </details>
      <details>
        <summary>Run-by-run clips</summary>
        <div class="run-grid">
          {build_run_cards(
              page_dir=page_dir,
              repeat_runs=repeat_runs,
              method_results_by_run=method_results_by_run,
          )}
        </div>
      </details>
    </div>
    """


def build_video_sections(
    *,
    page_dir: Path,
    repeat_runs: list[RepeatRun],
    videos: list[VideoStability],
) -> str:
    sections = []
    loaded_runs = [
        load_repeat_video_method_results(Path(repeat.run_dir)) for repeat in repeat_runs
    ]
    for video in videos:
        method_panels = []
        for method_name in ("pyscenedetect", "gemini"):
            method_panels.append(
                build_method_panel(
                    page_dir=page_dir,
                    repeat_runs=repeat_runs,
                    method_stability=video.methods[method_name],
                    method_results_by_run=[
                        loaded[video.video_name][method_name] for loaded in loaded_runs
                    ],
                )
            )
        video_unstable = any(method.unstable for method in video.methods.values())
        sections.append(
            "<section id='"
            + html.escape(video.video_slug)
            + "' class='video-section panel'>"
            + "<div class='video-head'>"
            + f"<div><h2>{html.escape(video.video_name)}</h2>"
            + f"<p class='muted'>PySceneDetect avg F1 {video.methods['pyscenedetect'].average_pairwise_f1:.3f} | "
            + f"Gemini avg F1 {video.methods['gemini'].average_pairwise_f1:.3f}</p></div>"
            + f"<span class='badge {'diff' if video_unstable else 'match'}'>{'UNSTABLE' if video_unstable else 'STABLE'}</span>"
            + "</div>"
            + "<div class='pair-grid'>"
            + "".join(method_panels)
            + "</div>"
            + "</section>"
        )
    return "\n".join(sections)


def build_repeat_rows(page_dir: Path, repeat_runs: list[RepeatRun]) -> str:
    rows = []
    for repeat in repeat_runs:
        run_dir = Path(repeat.run_dir)
        pairwise_rel = relative_path(page_dir, run_dir / "pairwise_comparison_report.html")
        differing_rel = relative_path(page_dir, run_dir / "pairwise_differing_report.html")
        rows.append(
            "<tr>"
            f"<td>Run {repeat.repeat_index}</td>"
            f"<td>{repeat.elapsed_seconds:.1f}s</td>"
            f"<td><a href='{html.escape(pairwise_rel)}'>pairwise report</a></td>"
            f"<td><a href='{html.escape(differing_rel)}'>differing only</a></td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_method_summary_rows(videos: list[VideoStability]) -> str:
    rows = []
    for method_name in ("pyscenedetect", "gemini"):
        method_values = [video.methods[method_name] for video in videos]
        avg_f1 = average([value.average_pairwise_f1 for value in method_values])
        min_f1 = min((value.minimum_pairwise_f1 for value in method_values), default=1.0)
        unstable_count = sum(1 for value in method_values if value.unstable)
        exact_pairs = sum(value.exact_match_pair_count for value in method_values)
        total_pairs = sum(len(value.pairwise_agreements) for value in method_values)
        rows.append(
            "<tr>"
            f"<td>{html.escape(method_name)}</td>"
            f"<td>{avg_f1:.3f}</td>"
            f"<td>{min_f1:.3f}</td>"
            f"<td>{exact_pairs}/{total_pairs}</td>"
            f"<td>{unstable_count}/{len(method_values)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_nav_cards(videos: list[VideoStability]) -> str:
    cards = []
    for video in videos:
        video_unstable = any(method.unstable for method in video.methods.values())
        cards.append(
            "<a class='nav-card' href='#"
            + html.escape(video.video_slug)
            + "'>"
            + f"<strong>{html.escape(video.video_name)}</strong>"
            + f"<span class='nav-meta {'diff' if video_unstable else 'match'}'>"
            + ("UNSTABLE" if video_unstable else "STABLE")
            + "</span>"
            + f"<span class='nav-sub'>PySceneDetect F1 {video.methods['pyscenedetect'].average_pairwise_f1:.3f} | "
            + f"Gemini F1 {video.methods['gemini'].average_pairwise_f1:.3f}</span>"
            + "</a>"
        )
    return "\n".join(cards)


def build_stability_report_html(
    *,
    session_dir: Path,
    title: str,
    summary_text: str,
    repeat_runs: list[RepeatRun],
    videos: list[VideoStability],
) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --ink: #181512;
      --muted: #655d56;
      --paper: rgba(255, 250, 245, 0.95);
      --panel: rgba(255, 255, 255, 0.95);
      --border: #d7ccc2;
      --accent: #7c3f20;
      --accent-soft: #f1e1d3;
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
        radial-gradient(circle at top left, #f8dfbe 0%, transparent 30%),
        radial-gradient(circle at top right, #ddeee7 0%, transparent 35%),
        linear-gradient(180deg, #f4eee6 0%, #ebe6de 100%);
    }}
    main {{ max-width: 1580px; margin: 0 auto; padding: 28px 18px 80px; }}
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
    .summary {{ color: var(--muted); max-width: 1000px; }}
    .top-links {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }}
    .top-links a {{
      text-decoration: none;
      padding: 8px 12px;
      border-radius: 999px;
      background: var(--accent-soft);
      border: 1px solid #e3cdbb;
    }}
    .nav-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
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
    .nav-meta {{
      display: inline-block;
      width: fit-content;
      padding: 4px 9px;
      border-radius: 999px;
      color: #fff;
      font-size: 0.85rem;
      font-weight: 600;
    }}
    .nav-meta.match {{ background: var(--match); }}
    .nav-meta.diff {{ background: var(--diff); }}
    .nav-sub {{ color: var(--muted); font-size: 0.95rem; }}
    .video-section {{ scroll-margin-top: 20px; }}
    .video-head {{
      display: flex;
      justify-content: space-between;
      align-items: start;
      gap: 16px;
      margin-bottom: 16px;
    }}
    .muted {{ color: var(--muted); }}
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
    .method-head {{
      display: flex;
      justify-content: space-between;
      align-items: start;
      gap: 14px;
      margin-bottom: 12px;
    }}
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
    .metric-card strong {{ display: block; font-size: 1.4rem; margin-bottom: 6px; }}
    .run-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      margin-top: 14px;
    }}
    .run-card {{
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 12px;
    }}
    .clip-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin: 12px 0;
    }}
    .clip-card {{
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 10px;
    }}
    .clip-card h4 {{ font-size: 0.95rem; }}
    .clip-card video {{
      width: 100%;
      border-radius: 10px;
      background: #000;
    }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }}
    th {{ background: rgba(124, 63, 32, 0.08); }}
    summary {{
      cursor: pointer;
      font-weight: 600;
      color: var(--accent);
    }}
    details[open] summary {{ margin-bottom: 10px; }}
    @media (max-width: 1200px) {{
      .run-grid {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 980px) {{
      .pair-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <div class="panel">
      <h1>{html.escape(title)}</h1>
      <p class="summary">{html.escape(summary_text)}</p>
      <div class="top-links">
        <a href="pairwise_stability_report.html">full stability report</a>
        <a href="pairwise_stability_differing_report.html">unstable only</a>
      </div>
      <div class="nav-grid">
        {build_nav_cards(videos)}
      </div>
    </div>

    <div class="panel">
      <h2>Run Timing</h2>
      <table>
        <thead>
          <tr>
            <th>Run</th>
            <th>Elapsed</th>
            <th>Pairwise Report</th>
            <th>Differing Report</th>
          </tr>
        </thead>
        <tbody>
          {build_repeat_rows(session_dir, repeat_runs)}
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>Method Stability Summary</h2>
      <table>
        <thead>
          <tr>
            <th>Method</th>
            <th>Average Pairwise F1</th>
            <th>Minimum Pairwise F1</th>
            <th>Exact-Match Pairs</th>
            <th>Unstable Videos</th>
          </tr>
        </thead>
        <tbody>
          {build_method_summary_rows(videos)}
        </tbody>
      </table>
    </div>

    {build_video_sections(page_dir=session_dir, repeat_runs=repeat_runs, videos=videos)}
  </main>
</body>
</html>
"""


def build_stability_payload(
    *,
    session_id: str,
    repeat_runs: list[RepeatRun],
    videos: list[VideoStability],
) -> dict[str, Any]:
    method_names = ("pyscenedetect", "gemini")
    method_summary: dict[str, Any] = {}
    for method_name in method_names:
        method_values = [video.methods[method_name] for video in videos]
        method_summary[method_name] = {
            "average_pairwise_f1": round(
                average([value.average_pairwise_f1 for value in method_values]), 4
            ),
            "minimum_pairwise_f1": min(
                (value.minimum_pairwise_f1 for value in method_values), default=1.0
            ),
            "unstable_video_count": sum(1 for value in method_values if value.unstable),
            "video_count": len(method_values),
            "exact_match_pairs": sum(value.exact_match_pair_count for value in method_values),
            "total_pairs": sum(len(value.pairwise_agreements) for value in method_values),
        }

    timings = [repeat.elapsed_seconds for repeat in repeat_runs]
    return {
        "session_id": session_id,
        "repeat_count": len(repeat_runs),
        "repeat_runs": [asdict(repeat) for repeat in repeat_runs],
        "timing": {
            "elapsed_seconds_by_run": timings,
            "average_elapsed_seconds": round(average(timings), 3),
            "minimum_elapsed_seconds": min(timings) if timings else 0.0,
            "maximum_elapsed_seconds": max(timings) if timings else 0.0,
        },
        "method_summary": method_summary,
        "videos": [video.to_dict() for video in videos],
    }


def main() -> int:
    args = parse_args()
    videos = select_videos(args)
    if not videos:
        raise SystemExit("No videos found to analyze.")
    if args.repeat_count < 2:
        raise SystemExit("--repeat-count must be at least 2 for stability analysis.")

    session_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = args.output_root.resolve() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        session_dir / "session_manifest.json",
        {
            "session_id": session_id,
            "repeat_count": args.repeat_count,
            "videos": [str(path) for path in videos],
            "glob": args.glob,
            "threshold": args.threshold,
            "min_scene_len_frames": args.min_scene_len,
            "boundary_tolerance_seconds": args.boundary_tolerance,
            "gemini_model": args.gemini_model,
            "prompt_path": str(args.prompt_path.resolve()),
            "env_file": str(args.env_file.resolve()),
        },
    )

    repeat_runs: list[RepeatRun] = []
    for repeat_index in range(1, args.repeat_count + 1):
        repeat_runs.append(
            run_repeat(
                repeat_index=repeat_index,
                session_dir=session_dir,
                videos=videos,
                args=args,
            )
        )

    videos_stability = build_video_stabilities(
        repeat_runs=repeat_runs,
        tolerance_seconds=args.boundary_tolerance,
    )
    unstable_videos = [
        video
        for video in videos_stability
        if any(method.unstable for method in video.methods.values())
    ]

    payload = build_stability_payload(
        session_id=session_id,
        repeat_runs=repeat_runs,
        videos=videos_stability,
    )
    write_json(session_dir / "stability_summary.json", payload)

    average_elapsed = payload["timing"]["average_elapsed_seconds"]
    write_text(
        session_dir / "pairwise_stability_report.html",
        build_stability_report_html(
            session_dir=session_dir,
            title="Repeated Cut Stability Report",
            summary_text=(
                f"Executed {args.repeat_count} full runs over {len(videos)} videos. "
                f"Average total elapsed time per run: {average_elapsed:.1f}s. "
                f"PySceneDetect stability is measured by pairwise agreement across runs, "
                f"and Gemini stability is measured the same way."
            ),
            repeat_runs=repeat_runs,
            videos=videos_stability,
        ),
    )
    write_text(
        session_dir / "pairwise_stability_differing_report.html",
        build_stability_report_html(
            session_dir=session_dir,
            title="Repeated Cut Stability Report (Unstable Only)",
            summary_text=(
                f"Only videos with at least one unstable method across {args.repeat_count} "
                f"runs are included here. Count: {len(unstable_videos)} / {len(videos_stability)}. "
                f"Average total elapsed time per run: {average_elapsed:.1f}s."
            ),
            repeat_runs=repeat_runs,
            videos=unstable_videos,
        ),
    )

    print(f"Saved stability artifacts to {session_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
