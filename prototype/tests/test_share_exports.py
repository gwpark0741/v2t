from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

from v2t_prototype.artifacts import ensure_run_manifest
from v2t_prototype.share_exports import export_first_share, export_second_share_bundle


def _make_run_dir(tmp_path: Path) -> tuple[Path, Path]:
    video_path = tmp_path / "videos" / "sample.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_text("video-bytes", encoding="utf-8")

    runs_dir = tmp_path / "runs"
    run_dir, _ = ensure_run_manifest(
        runs_dir=runs_dir,
        run_id="run_001",
        video_path=str(video_path.resolve()),
    )
    stage_06_dir = run_dir / "stage_06_agent_c"
    stage_06_dir.mkdir(parents=True, exist_ok=True)
    (stage_06_dir / "output.json").write_text(
        json.dumps({"pipeline_result": {"track_manifest": {"tracks": []}, "unresolved_unknowns": [], "warnings": []}}),
        encoding="utf-8",
    )
    report_path = run_dir / "pipeline_report.html"
    source_src = os.path.relpath(video_path.resolve(), report_path.parent.resolve())
    report_path.write_text(
        f'<html><body><video src="{source_src}"></video>'
        '<video src="stage_04_segment_prep/clips/CUT_001.mp4"></video></body></html>',
        encoding="utf-8",
    )
    clips_dir = run_dir / "stage_04_segment_prep" / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    (clips_dir / "CUT_001.mp4").write_text("clip-bytes", encoding="utf-8")
    return run_dir, video_path


def test_export_first_share_copies_video_and_final_output(tmp_path: Path):
    run_dir, video_path = _make_run_dir(tmp_path)

    result = export_first_share(run_dir, output_root=tmp_path / "exports" / "first")

    assert result.share_type == "first_share"
    assert result.output_path.is_dir()
    copied_video = result.output_path / f"input{video_path.suffix.lower()}"
    copied_output = result.output_path / "final_output.json"
    assert copied_video.read_text(encoding="utf-8") == "video-bytes"
    assert json.loads(copied_output.read_text(encoding="utf-8"))["pipeline_result"]["track_manifest"] == {"tracks": []}


def test_export_second_share_bundle_zips_video_and_report(tmp_path: Path):
    run_dir, video_path = _make_run_dir(tmp_path)

    result = export_second_share_bundle(run_dir, output_root=tmp_path / "exports" / "second")

    assert result.share_type == "second_share"
    assert result.output_path.name.endswith(".report_bundle.zip")
    with zipfile.ZipFile(result.output_path) as archive:
        slug = f"{video_path.stem}__{run_dir.name}"
        assert sorted(archive.namelist()) == [
            f"{slug}/clips/CUT_001.mp4",
            f"{slug}/report.html",
            f"{slug}/sample.mp4",
        ]
        report_html = archive.read(f"{slug}/report.html").decode("utf-8")
        assert 'src="sample.mp4"' in report_html
        assert 'src="clips/CUT_001.mp4"' in report_html
        assert "../../../videos/sample.mp4" not in report_html
        assert "stage_04_segment_prep/clips/CUT_001.mp4" not in report_html
        assert archive.read(f"{slug}/clips/CUT_001.mp4").decode("utf-8") == "clip-bytes"
