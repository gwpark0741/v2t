from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from .artifacts import load_run_manifest


@dataclass(frozen=True)
class ShareSource:
    run_dir: Path
    video_path: Path
    final_output_path: Path
    report_path: Path
    clip_paths: tuple[Path, ...]


@dataclass(frozen=True)
class ShareExportResult:
    run_dir: Path
    output_path: Path
    share_type: Literal["first_share", "second_share"]


_SRC_ATTR_RE = re.compile(r'(?P<prefix>\bsrc=)(?P<quote>["\'])(?P<value>.*?)(?P=quote)')


def _share_slug(run_dir: Path, video_path: Path) -> str:
    return f"{video_path.stem}__{run_dir.name}"


def _require_file(path: Path, *, label: str) -> Path:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def _load_share_source(run_dir: Path) -> ShareSource:
    resolved_run_dir = run_dir.expanduser().resolve()
    manifest = load_run_manifest(resolved_run_dir)
    video_path = _require_file(Path(manifest.video_path).expanduser().resolve(), label="source video")
    final_output_path = _require_file(
        resolved_run_dir / "stage_06_agent_c" / "output.json",
        label="stage 06 final output",
    )
    report_path = _require_file(
        resolved_run_dir / "pipeline_report.html",
        label="pipeline report",
    )
    clips_dir = resolved_run_dir / "stage_04_segment_prep" / "clips"
    clip_paths = tuple(
        sorted(path.resolve() for path in clips_dir.glob("*.mp4") if path.is_file())
    )
    return ShareSource(
        run_dir=resolved_run_dir,
        video_path=video_path,
        final_output_path=final_output_path,
        report_path=report_path,
        clip_paths=clip_paths,
    )


def export_first_share(run_dir: Path, *, output_root: Path) -> ShareExportResult:
    source = _load_share_source(run_dir)
    export_dir = output_root.expanduser().resolve() / _share_slug(source.run_dir, source.video_path)
    export_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source.video_path, export_dir / f"input{source.video_path.suffix.lower()}")
    shutil.copy2(source.final_output_path, export_dir / "final_output.json")
    return ShareExportResult(
        run_dir=source.run_dir,
        output_path=export_dir,
        share_type="first_share",
    )


def _rewrite_report_for_flat_bundle(source: ShareSource) -> str:
    report_html = source.report_path.read_text(encoding="utf-8")
    report_dir = source.report_path.parent.resolve()
    src_map = {
        os.path.relpath(source.video_path.resolve(), report_dir): source.video_path.name,
    }
    for clip_path in source.clip_paths:
        src_map[os.path.relpath(clip_path.resolve(), report_dir)] = f"clips/{clip_path.name}"

    def _replace_src(match: re.Match[str]) -> str:
        raw_value = html.unescape(match.group("value"))
        rewritten = src_map.get(raw_value)
        if rewritten is None:
            return match.group(0)
        quote = match.group("quote")
        return f'{match.group("prefix")}{quote}{html.escape(rewritten, quote=True)}{quote}'

    return _SRC_ATTR_RE.sub(_replace_src, report_html)


def export_second_share_bundle(run_dir: Path, *, output_root: Path) -> ShareExportResult:
    source = _load_share_source(run_dir)
    output_dir = output_root.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / f"{_share_slug(source.run_dir, source.video_path)}.report_bundle.zip"
    bundle_prefix = _share_slug(source.run_dir, source.video_path)
    rewritten_report = _rewrite_report_for_flat_bundle(source)

    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(source.video_path, arcname=str(Path(bundle_prefix) / source.video_path.name))
        archive.writestr(str(Path(bundle_prefix) / "report.html"), rewritten_report)
        for clip_path in source.clip_paths:
            archive.write(clip_path, arcname=str(Path(bundle_prefix) / "clips" / clip_path.name))
    return ShareExportResult(
        run_dir=source.run_dir,
        output_path=bundle_path,
        share_type="second_share",
    )


def export_first_share_batch(run_dirs: Sequence[Path], *, output_root: Path) -> list[ShareExportResult]:
    return [export_first_share(run_dir, output_root=output_root) for run_dir in run_dirs]


def export_second_share_batch(run_dirs: Sequence[Path], *, output_root: Path) -> list[ShareExportResult]:
    return [export_second_share_bundle(run_dir, output_root=output_root) for run_dir in run_dirs]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate shareable outputs from existing pipeline run directories.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    first_share = subparsers.add_parser(
        "first-share",
        help="Create immediate-share directories that contain the source video and final_output.json.",
    )
    first_share.add_argument("run_dirs", nargs="+", type=Path, help="Run directories under runs/<run_id>.")
    first_share.add_argument("--output-root", required=True, type=Path, help="Directory for first-share exports.")

    second_share = subparsers.add_parser(
        "second-share",
        help="Create review bundle ZIP files that preserve the report, source video, and segment clip relative paths.",
    )
    second_share.add_argument("run_dirs", nargs="+", type=Path, help="Run directories under runs/<run_id>.")
    second_share.add_argument("--output-root", required=True, type=Path, help="Directory for second-share ZIP bundles.")

    args = parser.parse_args(argv)
    if args.command == "first-share":
        results = export_first_share_batch(args.run_dirs, output_root=args.output_root)
    else:
        results = export_second_share_batch(args.run_dirs, output_root=args.output_root)

    for result in results:
        print(f"{result.share_type}\t{result.run_dir}\t{result.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
