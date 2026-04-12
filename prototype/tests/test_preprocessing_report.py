from pathlib import Path

from v2t_prototype.models import Cut, PreprocessingResult, VideoMetadata
from v2t_prototype.preprocessing_report import (
    build_preprocessing_report_html,
    write_preprocessing_report,
)


def _sample_result() -> PreprocessingResult:
    return PreprocessingResult(
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
    )


def test_build_preprocessing_report_html_contains_key_fields():
    html = build_preprocessing_report_html(_sample_result(), title="My Preprocessing Report")

    assert "My Preprocessing Report" in html
    assert "/tmp/sample_video.mp4" in html
    assert "FPS" in html
    assert "Frame Count" in html
    assert "Duration" in html
    assert "Resolution" in html
    assert "Cut Count" in html
    assert "CUT_001" in html
    assert "CUT_002" in html
    assert "timeline-track" in html


def test_write_preprocessing_report_writes_html_file(tmp_path: Path):
    output_path = tmp_path / "preprocessing_report.html"
    written_path = write_preprocessing_report(_sample_result(), output_path, title="Saved Report")

    assert written_path == output_path
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert "Saved Report" in content
    assert "<html" in content
