from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from v2t_prototype.models import (
    Cut,
    FullVideoAssetResult,
    LocalPreprocessingResult,
    VideoMetadata,
)
from v2t_prototype.segment_prep import run_segment_prep
from v2t_prototype.segment_prep import resolve_ffmpeg_bin


class _DummyUploaded:
    def __init__(self, uri: str, name: str):
        self.uri = uri
        self.name = name


def _make_full_asset(tmp_path: Path, cuts: list[Cut] | None = None) -> FullVideoAssetResult:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"dummy")
    metadata = VideoMetadata(
        video_path=str(video_path),
        fps=30.0,
        frame_count=300,
        duration_seconds=10.0,
        width=1280,
        height=720,
    )
    local = LocalPreprocessingResult(
        video_metadata=metadata,
        cuts=cuts or [Cut(id="CUT_001", start_time=0.0, end_time=5.0)],
        video_path=str(video_path),
        video_mime_type="video/mp4",
    )
    return FullVideoAssetResult(
        local=local,
        video_url="https://example.com/fullvideo",
        gemini_file_name="files/fullvideo",
        upload_timestamp_utc="2026-04-13T00:00:00Z",
    )


def test_run_segment_prep_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    asset = _make_full_asset(tmp_path)
    clips_dir = tmp_path / "clips"

    def fake_run(cmd, check, capture_output, text):
        Path(cmd[-1]).write_bytes(b"clip")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("v2t_prototype.segment_prep.subprocess.run", fake_run)
    monkeypatch.setattr(
        "v2t_prototype.segment_prep.upload_video_file",
        lambda client, path: _DummyUploaded("clip://url", "files/clip"),
    )
    monkeypatch.setattr(
        "v2t_prototype.segment_prep.wait_for_uploaded_file_active",
        lambda client, uploaded: uploaded,
    )
    monkeypatch.setattr(
        "v2t_prototype.segment_prep.get_uploaded_video_url",
        lambda uploaded: uploaded.uri,
    )
    monkeypatch.setattr(
        "v2t_prototype.segment_prep.get_uploaded_file_name",
        lambda uploaded: uploaded.name,
    )

    result = run_segment_prep(asset, clips_dir=clips_dir, client=object())

    assert len(result.clips) == 1
    assert result.clips[0].cut_id == "CUT_001"
    assert result.clips[0].clip_video_url == "clip://url"
    assert result.clips[0].clip_video_mime_type == "video/mp4"
    assert Path(result.clips[0].local_clip_path).exists()
    assert result.skipped_cuts == []
    assert result.warnings == []


def test_run_segment_prep_uses_authoritative_cut_interval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    asset = _make_full_asset(
        tmp_path,
        cuts=[Cut(id="CUT_001", start_time=1.25, end_time=3.75)],
    )
    clips_dir = tmp_path / "clips"
    recorded_commands: list[list[str]] = []

    def fake_run(cmd, check, capture_output, text):
        recorded_commands.append(cmd)
        Path(cmd[-1]).write_bytes(b"clip")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("v2t_prototype.segment_prep.subprocess.run", fake_run)
    monkeypatch.setattr(
        "v2t_prototype.segment_prep.upload_video_file",
        lambda client, path: _DummyUploaded("clip://url", "files/clip"),
    )
    monkeypatch.setattr(
        "v2t_prototype.segment_prep.wait_for_uploaded_file_active",
        lambda client, uploaded: uploaded,
    )
    monkeypatch.setattr(
        "v2t_prototype.segment_prep.get_uploaded_video_url",
        lambda uploaded: uploaded.uri,
    )
    monkeypatch.setattr(
        "v2t_prototype.segment_prep.get_uploaded_file_name",
        lambda uploaded: uploaded.name,
    )

    run_segment_prep(asset, clips_dir=clips_dir, client=object())

    assert len(recorded_commands) == 1
    assert recorded_commands[0][1:8] == [
        "-y",
        "-ss",
        "1.250",
        "-i",
        str(Path(asset.local.video_path)),
        "-t",
        "2.500",
    ]


def test_run_segment_prep_records_skipped_cut_and_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    asset = _make_full_asset(
        tmp_path,
        cuts=[
            Cut(id="CUT_001", start_time=0.0, end_time=4.0),
            Cut(id="CUT_002", start_time=4.0, end_time=8.0),
        ],
    )
    clips_dir = tmp_path / "clips"

    def fake_run(cmd, check, capture_output, text):
        output_path = Path(cmd[-1])
        if output_path.name == "CUT_002.mp4":
            raise subprocess.CalledProcessError(1, cmd, stderr="ffmpeg failed")
        output_path.write_bytes(b"clip")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("v2t_prototype.segment_prep.subprocess.run", fake_run)
    monkeypatch.setattr(
        "v2t_prototype.segment_prep.upload_video_file",
        lambda client, path: _DummyUploaded(f"clip://{Path(path).stem}", f"files/{Path(path).stem}"),
    )
    monkeypatch.setattr(
        "v2t_prototype.segment_prep.wait_for_uploaded_file_active",
        lambda client, uploaded: uploaded,
    )
    monkeypatch.setattr(
        "v2t_prototype.segment_prep.get_uploaded_video_url",
        lambda uploaded: uploaded.uri,
    )
    monkeypatch.setattr(
        "v2t_prototype.segment_prep.get_uploaded_file_name",
        lambda uploaded: uploaded.name,
    )

    result = run_segment_prep(asset, clips_dir=clips_dir, client=object())

    assert len(result.clips) == 1
    assert result.clips[0].cut_id == "CUT_001"
    assert len(result.skipped_cuts) == 1
    assert result.skipped_cuts[0].cut_id == "CUT_002"
    assert result.skipped_cuts[0].reason == "FFMPEG_FAILURE"
    assert [warning.code for warning in result.warnings] == ["SEGMENT_PREP_FFMPEG_FAILURE"]


def test_run_segment_prep_raises_when_no_clips_succeed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    asset = _make_full_asset(tmp_path)

    def failing_run(cmd, check, capture_output, text):
        raise subprocess.CalledProcessError(1, cmd, stderr="ffmpeg failed")

    monkeypatch.setattr("v2t_prototype.segment_prep.subprocess.run", failing_run)

    with pytest.raises(ValueError):
        run_segment_prep(asset, clips_dir=tmp_path / "clips", client=object())


def test_resolve_ffmpeg_bin_falls_back_to_imageio_binary(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("v2t_prototype.segment_prep.which", lambda _: None)
    monkeypatch.setattr(
        "v2t_prototype.segment_prep.imageio_ffmpeg.get_ffmpeg_exe",
        lambda: "/tmp/uv-ffmpeg",
    )

    assert resolve_ffmpeg_bin() == "/tmp/uv-ffmpeg"
