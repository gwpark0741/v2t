from pathlib import Path

from v2t_prototype.models import Cut, FullVideoAssetResult, LocalPreprocessingResult, VideoMetadata, WarningItem
from v2t_prototype.full_video_asset_report import (
    build_full_video_asset_report_html,
    write_full_video_asset_report,
)


def _sample_result() -> FullVideoAssetResult:
    local = LocalPreprocessingResult(
        video_metadata=VideoMetadata(
            video_path="/tmp/sample_video.mp4",
            fps=24.0,
            frame_count=240,
            duration_seconds=10.0,
            width=1920,
            height=1080,
        ),
        cuts=[
            Cut(id="CUT_001", start_time=0.0, end_time=4.0),
            Cut(id="CUT_002", start_time=4.0, end_time=10.0),
        ],
        video_path="/tmp/sample_video.mp4",
        video_mime_type="video/mp4",
    )
    return FullVideoAssetResult(
        local=local,
        video_url="https://generativelanguage.googleapis.com/v1beta/files/abc123",
        gemini_file_name="files/abc123",
        upload_timestamp_utc="2026-04-13T07:30:00Z",
    )


def test_build_full_video_asset_report_html_contains_key_fields():
    warnings = [
        WarningItem(
            code="UPLOAD_RETRIED_ONCE",
            severity="warning",
            message="Upload succeeded after one retry.",
            context={"attempt_count": 2},
        )
    ]

    html = build_full_video_asset_report_html(_sample_result(), warnings=warnings)

    assert "Full Video Asset Report" in html
    assert "ACTIVE" in html
    assert "https://generativelanguage.googleapis.com/v1beta/files/abc123" in html
    assert "files/abc123" in html
    assert "Cut Count" in html
    assert "Warnings" in html
    assert "UPLOAD_RETRIED_ONCE" in html


def test_write_full_video_asset_report_writes_html_file(tmp_path: Path):
    output_path = tmp_path / "full_video_asset_report.html"
    written_path = write_full_video_asset_report(_sample_result(), output_path, title="Saved Asset Report")

    assert written_path == output_path
    content = output_path.read_text(encoding="utf-8")
    assert "Saved Asset Report" in content
    assert "<html" in content
