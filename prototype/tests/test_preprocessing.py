from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
import pytest
from google.genai import types as genai_types

from v2t_prototype.models import Cut, PreprocessingResult, VideoMetadata
from v2t_prototype.preprocessing import (
    detect_cuts,
    prepare_full_video_asset,
    run_local_preprocessing,
    run_preprocessing,
)


def _write_synthetic_video(path: Path) -> tuple[float, int]:
    fps = 10.0
    width, height = 96, 64
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Failed to create test video: {path}")

    # 장면 전환이 확실히 드러나도록 단색 블록을 순차적으로 기록합니다.
    # (검출 민감도 파라미터를 낮춰 테스트 환경 간 편차를 줄입니다.)
    colors = [
        (0, 0, 0),       # black
        (255, 255, 255), # white
        (0, 255, 0),     # green
    ]
    frames_per_scene = 15
    total_frames = 0
    for color in colors:
        frame = np.full((height, width, 3), color, dtype=np.uint8)
        for _ in range(frames_per_scene):
            writer.write(frame)
            total_frames += 1
    writer.release()
    return fps, total_frames


def test_cut_model_rejects_invalid_range():
    with pytest.raises(ValueError):
        Cut(id="CUT_001", start_time=1.0, end_time=1.0)


def test_preprocessing_result_requires_non_empty_cuts():
    metadata = VideoMetadata(
        video_path="/tmp/dummy.mp4",
        fps=24.0,
        frame_count=240,
        duration_seconds=10.0,
        width=1920,
        height=1080,
    )
    with pytest.raises(ValueError):
        PreprocessingResult(video_metadata=metadata, cuts=[], video_url="gs://bucket/dummy.mp4")


def test_detect_cuts_assigns_sequential_cut_ids(tmp_path: Path):
    video_path = tmp_path / "synthetic_cuts.avi"
    _write_synthetic_video(video_path)

    cuts = detect_cuts(
        video_path,
        adaptive_threshold=1.0,
        min_scene_len=5,
        window_width=2,
        min_content_val=5.0,
    )

    assert cuts, "At least one cut segment should always exist."
    expected_ids = [f"CUT_{index:03d}" for index in range(1, len(cuts) + 1)]
    assert [cut.id for cut in cuts] == expected_ids
    assert cuts[0].start_time == 0.0
    assert all(cuts[index].end_time <= cuts[index + 1].start_time for index in range(len(cuts) - 1))


def test_run_preprocessing_returns_metadata_and_cuts(tmp_path: Path):
    video_path = tmp_path / "synthetic_preprocessing.avi"
    fps, frame_count = _write_synthetic_video(video_path)
    uploaded_file = SimpleNamespace(
        name="files/preprocessing_video",
        uri="gs://bucket/preprocessing_video.mp4",
        state=genai_types.FileState.ACTIVE,
    )

    with patch("v2t_prototype.preprocessing.upload_video_file", return_value=uploaded_file), patch(
        "v2t_prototype.preprocessing.wait_for_uploaded_file_active", return_value=uploaded_file
    ), patch("v2t_prototype.preprocessing.guess_type", return_value=("video/mp4", None)):
        result = run_preprocessing(
            video_path,
            adaptive_threshold=1.0,
            min_scene_len=5,
            window_width=2,
            min_content_val=5.0,
        )

    assert result.video_metadata.frame_count == frame_count
    assert result.video_metadata.fps == pytest.approx(fps, abs=0.1)
    assert result.video_metadata.width == 96
    assert result.video_metadata.height == 64
    assert result.cuts
    assert result.cuts[0].start_time == 0.0
    assert result.cuts[-1].end_time == pytest.approx(result.video_metadata.duration_seconds, abs=0.1)
    assert result.video_mime_type == "video/mp4"


def test_run_preprocessing_uploads_video_and_returns_video_url(tmp_path: Path):
    video_path = tmp_path / "synthetic_preprocessing.avi"
    _write_synthetic_video(video_path)
    client = Mock()
    uploaded_file = SimpleNamespace(
        name="files/uploaded_video",
        uri="gs://bucket/uploaded_video.mp4",
        state=genai_types.FileState.ACTIVE,
    )

    with patch("v2t_prototype.preprocessing.upload_video_file", return_value=uploaded_file) as upload_mock, patch(
        "v2t_prototype.preprocessing.wait_for_uploaded_file_active", return_value=uploaded_file
    ) as wait_mock, patch("v2t_prototype.preprocessing.guess_type", return_value=("video/mp4", None)):
        result = run_preprocessing(
            video_path,
            adaptive_threshold=1.0,
            min_scene_len=5,
            window_width=2,
            min_content_val=5.0,
            client=client,
        )

    upload_mock.assert_called_once_with(client, video_path)
    wait_mock.assert_called_once_with(client, uploaded_file)
    assert result.video_url == "gs://bucket/uploaded_video.mp4"
    assert result.video_mime_type == "video/mp4"


def test_run_local_preprocessing_returns_metadata_and_cuts(tmp_path: Path):
    video_path = tmp_path / "synthetic_local_preprocessing.avi"
    fps, frame_count = _write_synthetic_video(video_path)

    metadata, cuts = run_local_preprocessing(
        video_path,
        adaptive_threshold=1.0,
        min_scene_len=5,
        window_width=2,
        min_content_val=5.0,
    )

    assert metadata.frame_count == frame_count
    assert metadata.fps == pytest.approx(fps, abs=0.1)
    assert cuts
    assert cuts[0].start_time == 0.0


def test_prepare_full_video_asset_uploads_and_returns_url_and_mime(tmp_path: Path):
    video_path = tmp_path / "synthetic_asset.avi"
    _write_synthetic_video(video_path)
    metadata = VideoMetadata(
        video_path=str(video_path),
        fps=10.0,
        frame_count=45,
        duration_seconds=4.5,
        width=96,
        height=64,
    )
    client = Mock()
    uploaded_file = SimpleNamespace(
        name="files/preprocessing_asset",
        uri="gs://bucket/preprocessing_asset.mp4",
        state=genai_types.FileState.ACTIVE,
    )

    with patch("v2t_prototype.preprocessing.upload_video_file", return_value=uploaded_file) as upload_mock, patch(
        "v2t_prototype.preprocessing.wait_for_uploaded_file_active", return_value=uploaded_file
    ) as wait_mock, patch("v2t_prototype.preprocessing.guess_type", return_value=("video/mp4", None)):
        video_url, video_mime_type = prepare_full_video_asset(
            video_path=video_path,
            metadata=metadata,
            client=client,
        )

    upload_mock.assert_called_once_with(client, video_path)
    wait_mock.assert_called_once_with(client, uploaded_file)
    assert video_url == "gs://bucket/preprocessing_asset.mp4"
    assert video_mime_type == "video/mp4"
