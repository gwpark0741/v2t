from pathlib import Path

from v2t_prototype.agent_a_report import build_agent_a_report_html, write_agent_a_report
from v2t_prototype.agent_a_runtime import AgentARuntimeOutput
from v2t_prototype.models import (
    AgentARequest,
    AgentAResponse,
    AmbienceSource,
    Character,
    Cut,
    EntityRegistry,
    FullVideoAssetResult,
    Interval,
    KeyObject,
    LocalPreprocessingResult,
    VideoMetadata,
)


def _make_sample_full_video_asset_result() -> FullVideoAssetResult:
    metadata = VideoMetadata(
        video_path="videos/sample.mp4",
        fps=30.0,
        frame_count=300,
        duration_seconds=10.0,
        width=1280,
        height=720,
    )
    cuts = [
        Cut(id="CUT_001", start_time=0.0, end_time=5.0),
        Cut(id="CUT_002", start_time=5.0, end_time=10.0),
    ]
    return FullVideoAssetResult(
        local=LocalPreprocessingResult(
            video_metadata=metadata,
            cuts=cuts,
            video_path="videos/sample.mp4",
            video_mime_type="video/mp4",
        ),
        video_url="gs://test-bucket/video.mp4",
        gemini_file_name="files/sample",
        upload_timestamp_utc="2026-04-14T00:00:00Z",
    )


def _make_sample_runtime_output(full_video_asset: FullVideoAssetResult) -> AgentARuntimeOutput:
    request = AgentARequest(
        video_url=full_video_asset.video_url,
        video_mime_type=full_video_asset.local.video_mime_type,
        video_metadata=full_video_asset.local.video_metadata,
        cuts=full_video_asset.local.cuts,
    )
    response = AgentAResponse(
        entity_registry=EntityRegistry(
            characters=[
                Character(
                    id="char_001",
                    label="Lead",
                    visual_description="Runs",
                    entry_exit_intervals=[Interval(start_time=0.0, end_time=10.0)],
                    audibility="audible",
                )
            ],
            key_objects=[
                KeyObject(
                    id="obj_ball",
                    label="Ball",
                    visual_description="White ball",
                    material="leather",
                    surface="smooth",
                    has_mechanism=False,
                    audibility="audible",
                )
            ],
            ambience_sources=[
                AmbienceSource(
                    id="amb_crowd",
                    label="Crowd",
                    space_description="Stands",
                    distance_profile="far",
                    tonal_quality="cheerful",
                )
            ],
        ),
    )
    return AgentARuntimeOutput(request=request, response=response, raw_response_text="{}")


def test_build_agent_a_report_html_includes_sections():
    full_video_asset = _make_sample_full_video_asset_result()
    runtime_output = _make_sample_runtime_output(full_video_asset)
    html = build_agent_a_report_html(full_video_asset, runtime_output, title="Agent Report")

    assert "Agent Report" in html
    assert "video-player" in html
    assert "CUT_001" in html
    assert "Input Video Summary" in html
    assert "Upload Result" in html
    assert "Preprocessing Output" in html
    assert "Agent A Request Summary" in html
    assert "Agent A Request JSON" in html
    assert "Agent A Response Summary" in html
    assert "Raw Gemini JSON Text" in html
    assert "video_mime_type" in html
    assert "gs://test-bucket/video.mp4" in html
    assert "Characters" in html
    assert "Key Objects" in html
    assert "Ambience Sources" in html
    assert "Validation Summary" in html
    assert "PASS" in html
    assert "No validation issues detected." in html


def test_write_agent_a_report_creates_file(tmp_path: Path):
    full_video_asset = _make_sample_full_video_asset_result()
    runtime_output = _make_sample_runtime_output(full_video_asset)
    output_file = tmp_path / "report.html"

    result_path = write_agent_a_report(full_video_asset, runtime_output, output_file, title="Report")

    assert result_path == output_file
    content = output_file.read_text()
    assert "<h1>Report</h1>" in content
    assert "CUT_002" in content
    assert "Agent A Request JSON" in content
    assert "Raw Gemini JSON Text" in content
