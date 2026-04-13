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
    Interval,
    KeyObject,
    PreprocessingResult,
    VideoMetadata,
)


def _make_sample_preprocessing_result() -> PreprocessingResult:
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
    return PreprocessingResult(
        video_metadata=metadata,
        cuts=cuts,
        video_url="gs://test-bucket/video.mp4",
        video_mime_type="video/mp4",
    )


def _make_sample_runtime_output(preprocessing: PreprocessingResult) -> AgentARuntimeOutput:
    request = AgentARequest(
        video_url=preprocessing.video_url,
        video_mime_type=preprocessing.video_mime_type,
        video_metadata=preprocessing.video_metadata,
        cuts=preprocessing.cuts,
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
    preprocessing = _make_sample_preprocessing_result()
    runtime_output = _make_sample_runtime_output(preprocessing)
    html = build_agent_a_report_html(preprocessing, runtime_output, title="Agent Report")

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
    preprocessing = _make_sample_preprocessing_result()
    runtime_output = _make_sample_runtime_output(preprocessing)
    output_file = tmp_path / "report.html"

    result_path = write_agent_a_report(preprocessing, runtime_output, output_file, title="Report")

    assert result_path == output_file
    content = output_file.read_text()
    assert "<h1>Report</h1>" in content
    assert "CUT_002" in content
    assert "Agent A Request JSON" in content
    assert "Raw Gemini JSON Text" in content
