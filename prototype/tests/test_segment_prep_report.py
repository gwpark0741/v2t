from __future__ import annotations

from pathlib import Path

from v2t_prototype.models import SegmentClip, SegmentPrepResult, SkippedCut, WarningItem
from v2t_prototype.segment_prep_report import (
    build_segment_prep_report_html,
    write_segment_prep_report,
)


def _sample_result() -> SegmentPrepResult:
    clip = SegmentClip(
        cut_id="CUT_001",
        local_clip_path="/tmp/CUT_001.mp4",
        clip_video_url="https://example.com/cut01",
        clip_gemini_file_name="files/cut01",
        clip_video_mime_type="video/mp4",
        padded_start_time=0.0,
        padded_end_time=5.5,
        actual_padding_start=1.0,
        actual_padding_end=0.5,
    )
    skipped = SkippedCut(cut_id="CUT_002", reason="FFMPEG_FAILURE", error_detail="boom")
    warning = WarningItem(code="SEGMENT_PREP_FFMPEG_FAILURE", severity="warning", message="clip failed", context={})
    return SegmentPrepResult(clips=[clip], skipped_cuts=[skipped], warnings=[warning])


def test_build_segment_prep_report_html_contains_key_fields():
    html = build_segment_prep_report_html(
        _sample_result(),
        source_video_path="/tmp/source.mp4",
        title="Segment Prep Report",
    )

    assert "Segment Prep Report" in html
    assert "/tmp/source.mp4" in html
    assert "CUT_001" in html
    assert "CUT_002" in html
    assert "https://example.com/cut01" in html
    assert "Skipped Cuts" in html
    assert "Warnings" in html


def test_write_segment_prep_report_writes_html_file(tmp_path: Path):
    output_path = tmp_path / "segment_prep_report.html"
    written_path = write_segment_prep_report(
        _sample_result(),
        output_path,
        source_video_path="/tmp/source.mp4",
        title="Saved Segment Prep Report",
    )

    assert written_path == output_path
    content = output_path.read_text(encoding="utf-8")
    assert "Saved Segment Prep Report" in content
    assert "<html" in content
