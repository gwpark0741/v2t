from __future__ import annotations

import json
from pathlib import Path

from v2t_prototype.artifacts import LOCAL_PREPROCESSING_STAGE_DIR, write_local_preprocessing_artifacts
from v2t_prototype.models import Cut, LocalPreprocessingResult, VideoMetadata, WarningItem


def _sample_local_result() -> LocalPreprocessingResult:
    return LocalPreprocessingResult(
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


def test_write_local_preprocessing_artifacts_writes_canonical_files(tmp_path: Path):
    result = _sample_local_result()
    warnings = [
        WarningItem(
            code="CUT_ENDS_BEFORE_VIDEO_DURATION",
            severity="warning",
            message="The last cut does not align with the full video duration.",
            context={"cut_id": "CUT_002"},
        )
    ]

    stage_dir = write_local_preprocessing_artifacts(
        result,
        runs_dir=tmp_path,
        run_id="run_001",
        warnings=warnings,
        report_title="Stage 01 Report",
    )

    assert stage_dir == tmp_path / "run_001" / LOCAL_PREPROCESSING_STAGE_DIR
    assert (stage_dir / "output.json").exists()
    assert (stage_dir / "warnings.json").exists()
    assert (stage_dir / "report.html").exists()

    output_payload = json.loads((stage_dir / "output.json").read_text(encoding="utf-8"))
    warnings_payload = json.loads((stage_dir / "warnings.json").read_text(encoding="utf-8"))

    assert output_payload["video_path"] == "/tmp/sample_video.mp4"
    assert output_payload["cuts"][0]["id"] == "CUT_001"
    assert warnings_payload["stage"] == LOCAL_PREPROCESSING_STAGE_DIR
    assert warnings_payload["warnings"][0]["code"] == "CUT_ENDS_BEFORE_VIDEO_DURATION"
    assert "Stage 01 Report" in (stage_dir / "report.html").read_text(encoding="utf-8")
