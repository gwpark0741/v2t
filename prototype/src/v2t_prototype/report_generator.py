from __future__ import annotations

import argparse
from pathlib import Path

from .report_stages import (
    tab_01_cuts,
    tab_02_upload,
    tab_03_agent_a,
    tab_04_segments,
    tab_05_agent_b,
    tab_06_tracks,
)
from .report_stages.common import render_tab_with_isolation, safe_load_json, wrap_report_page


def generate_pipeline_report(run_dir: Path) -> Path:
    report_path = run_dir / "pipeline_report.html"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = safe_load_json(run_dir / "run_manifest.json")
    run_id = run_dir.name
    if isinstance(manifest, dict):
        run_id = str(manifest.get("run_id") or run_id)

    tabs = [
        ("cuts", "01 Cuts", render_tab_with_isolation("Stage 01", lambda: tab_01_cuts.render_tab(run_dir, report_path))),
        ("upload", "02 Upload", render_tab_with_isolation("Stage 02", lambda: tab_02_upload.render_tab(run_dir, report_path))),
        ("agent-a", "03 Agent A", render_tab_with_isolation("Stage 03", lambda: tab_03_agent_a.render_tab(run_dir, report_path))),
        ("segments", "04 Segments", render_tab_with_isolation("Stage 04", lambda: tab_04_segments.render_tab(run_dir, report_path))),
        ("agent-b", "05 Agent B", render_tab_with_isolation("Stage 05", lambda: tab_05_agent_b.render_tab(run_dir, report_path))),
        ("tracks", "06 Tracks", render_tab_with_isolation("Stage 06", lambda: tab_06_tracks.render_tab(run_dir, report_path))),
    ]
    html = wrap_report_page(run_id=run_id, title="Pipeline Report", tabs=tabs)
    report_path.write_text(html, encoding="utf-8")
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a single HTML pipeline report for one run directory.")
    parser.add_argument("run_dir", type=Path, help="Path to runs/<run_id>")
    args = parser.parse_args(argv)
    output_path = generate_pipeline_report(args.run_dir)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
