from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2
from google import genai
from scenedetect import SceneManager, open_video
from scenedetect.detectors import AdaptiveDetector

from .gemini_client import (
    create_gemini_client,
    get_uploaded_video_url,
    upload_video_file,
    wait_for_uploaded_file_active,
)
from .models import Cut, PreprocessingResult, VideoMetadata


def extract_video_metadata(video_path: Path) -> VideoMetadata:
    """입력 영상의 핵심 메타데이터(fps/프레임 수/해상도/길이)를 추출합니다.

    전처리의 모든 후속 단계(컷 범위 계산, 경계 검증)가 이 값을 기준으로 동작하므로,
    여기서 실패하면 즉시 예외를 발생시켜 잘못된 입력을 빠르게 차단합니다.
    """
    resolved_path = video_path.expanduser().resolve()
    if not resolved_path.is_file():
        raise FileNotFoundError(f"Video file not found: {resolved_path}")

    capture = cv2.VideoCapture(str(resolved_path))
    if not capture.isOpened():
        raise RuntimeError(f"Failed to open video: {resolved_path}")

    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        capture.release()

    if fps <= 0.0 or frame_count <= 0:
        raise RuntimeError(f"Invalid video metadata for: {resolved_path}")

    return VideoMetadata(
        video_path=str(resolved_path),
        fps=fps,
        frame_count=frame_count,
        duration_seconds=frame_count / fps,
        width=width,
        height=height,
    )


def _build_cuts_from_boundaries(*, boundaries: list[float], duration_seconds: float) -> list[Cut]:
    """씬 시작 경계 목록을 연속 컷 구간으로 변환합니다.

    - 경계는 영상 길이 내부(0 < t < duration)만 채택합니다.
    - 0초와 영상 종료 시점을 자동으로 붙여 전체 구간을 빠짐없이 덮습니다.
    - timestamp grid 스냅은 하지 않고, 부동소수 흔들림만 줄이기 위해 소수점 3자리로 정리합니다.
    """
    clean_boundaries = sorted(
        {
            round(boundary, 3)
            for boundary in boundaries
            if 0.0 < boundary < duration_seconds
        }
    )
    segment_edges = [0.0, *clean_boundaries, round(duration_seconds, 3)]
    cuts: list[Cut] = []

    for index, (start_time, end_time) in enumerate(zip(segment_edges, segment_edges[1:]), start=1):
        start_time = round(start_time, 3)
        end_time = round(end_time, 3)
        if end_time <= start_time:
            continue
        cuts.append(Cut(id=f"CUT_{index:03d}", start_time=start_time, end_time=end_time))

    if not cuts:
        return [Cut(id="CUT_001", start_time=0.0, end_time=round(duration_seconds, 3))]
    return cuts


def _detect_cuts_with_metadata(
    *,
    video_path: Path,
    metadata: VideoMetadata,
    adaptive_threshold: float,
    min_scene_len: int,
    window_width: int,
    min_content_val: float,
) -> list[Cut]:
    """AdaptiveDetector를 실행해 authoritative cut boundary를 생성합니다."""
    video = open_video(str(video_path.expanduser().resolve()))
    scene_manager = SceneManager()
    scene_manager.add_detector(
        AdaptiveDetector(
            adaptive_threshold=adaptive_threshold,
            min_scene_len=min_scene_len,
            window_width=window_width,
            min_content_val=min_content_val,
        )
    )
    scene_manager.detect_scenes(video=video, show_progress=False)

    # start_in_scene=True를 사용하면 첫 컷 시작점(0.0s)을 포함한 전체 씬 목록을 얻습니다.
    # 우리는 "다음 씬 시작 시점들"을 경계로 사용하므로 첫 원소를 제외합니다.
    scene_list = scene_manager.get_scene_list(start_in_scene=True)
    boundaries = [start_time.get_seconds() for start_time, _ in scene_list[1:]]
    return _build_cuts_from_boundaries(boundaries=boundaries, duration_seconds=metadata.duration_seconds)


def detect_cuts(
    video_path: Path,
    *,
    adaptive_threshold: float = 4.0,
    min_scene_len: int = 30,
    window_width: int = 2,
    min_content_val: float = 15.0,
) -> list[Cut]:
    """영상에서 컷 경계를 검출해 `Cut[]`를 반환합니다.

    기본 파라미터는 v4 스펙의 최신 기본값(AdaptiveDetector 4.0/30/2/15.0)을 그대로 사용합니다.
    """
    metadata = extract_video_metadata(video_path)
    return _detect_cuts_with_metadata(
        video_path=video_path,
        metadata=metadata,
        adaptive_threshold=adaptive_threshold,
        min_scene_len=min_scene_len,
        window_width=window_width,
        min_content_val=min_content_val,
    )


def run_preprocessing(
    video_path: Path,
    *,
    adaptive_threshold: float = 4.0,
    min_scene_len: int = 30,
    window_width: int = 2,
    min_content_val: float = 15.0,
    client: Optional[genai.Client] = None,
) -> PreprocessingResult:
    """전처리 엔트리포인트입니다.

    반환 계약은 단순하게 유지합니다:
    1) 영상 물리 메타데이터 추출
    2) AdaptiveDetector 기반 컷 경계 확정
    3) 두 결과를 `PreprocessingResult`로 묶어 반환
    4) Gemini Files API에 업로드하고 `video_url`을 포함
    """
    metadata = extract_video_metadata(video_path)
    cuts = _detect_cuts_with_metadata(
        video_path=video_path,
        metadata=metadata,
        adaptive_threshold=adaptive_threshold,
        min_scene_len=min_scene_len,
        window_width=window_width,
        min_content_val=min_content_val,
    )
    runtime_client = client or create_gemini_client()
    uploaded_file = upload_video_file(runtime_client, video_path)
    uploaded_file = wait_for_uploaded_file_active(runtime_client, uploaded_file)
    video_url = get_uploaded_video_url(uploaded_file)
    return PreprocessingResult(
        video_metadata=metadata,
        cuts=cuts,
        video_url=video_url,
    )
