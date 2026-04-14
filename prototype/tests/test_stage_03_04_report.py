from __future__ import annotations

from pathlib import Path

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
    SegmentClip,
    SegmentPrepResult,
    VideoMetadata,
)
from v2t_prototype.stage_03_04_report import (
    build_stage_03_04_report_html,
    write_stage_03_04_report,
)


def _sample_preprocessing() -> PreprocessingResult:
    return PreprocessingResult(
        video_metadata=VideoMetadata(
            video_path="/tmp/source.mp4",
            fps=24.0,
            frame_count=240,
            duration_seconds=10.0,
            width=1280,
            height=720,
        ),
        cuts=[
            Cut(id="CUT_001", start_time=0.0, end_time=4.0),
            Cut(id="CUT_002", start_time=4.0, end_time=10.0),
        ],
        video_url="https://example.com/full",
        video_mime_type="video/mp4",
    )


def _sample_agent_a_output() -> AgentARuntimeOutput:
    preprocessing = _sample_preprocessing()
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
                    label="Fighter A",
                    visual_description="Armored fighter",
                    entry_exit_intervals=[Interval(start_time=0.0, end_time=10.0)],
                    audibility="likely_audible",
                )
            ],
            key_objects=[
                KeyObject(
                    id="obj_001",
                    label="Sword",
                    visual_description="Metal sword",
                    material="steel",
                    surface="polished",
                    has_mechanism=False,
                    audibility="audible",
                )
            ],
            ambience_sources=[
                AmbienceSource(
                    id="amb_001",
                    label="Training yard",
                    space_description="Open outdoor arena",
                    distance_profile="mid",
                    tonal_quality="dry",
                )
            ],
        ),
    )
    return AgentARuntimeOutput(
        request=request,
        response=response,
        raw_response_text=response.model_dump_json(),
    )


def _sample_segment_prep() -> SegmentPrepResult:
    return SegmentPrepResult(
        clips=[
            SegmentClip(
                cut_id="CUT_001",
                local_clip_path="/tmp/CUT_001.mp4",
                clip_video_url="https://example.com/cut001",
                clip_gemini_file_name="files/cut001",
                clip_video_mime_type="video/mp4",
            )
        ],
        skipped_cuts=[],
        warnings=[],
    )


def test_build_stage_03_04_report_html_contains_pairwise_content():
    html = build_stage_03_04_report_html(
        _sample_preprocessing(),
        _sample_agent_a_output(),
        _sample_segment_prep(),
        title="Pairwise Review",
    )

    assert "Pairwise Review" in html
    assert "CUT_001" in html
    assert "https://example.com/cut001" in html
    assert "No segment clip available." in html
    assert "Authoritative Cut" in html
    assert "Agent A Entity Registry" in html
    assert "Fighter A" in html
    assert "Sword" in html
    assert "Training yard" in html
    assert "Padded Start" not in html
    assert "Padded End" not in html


def test_write_stage_03_04_report_writes_file(tmp_path: Path):
    output_path = tmp_path / "pairwise_report.html"
    written = write_stage_03_04_report(
        _sample_preprocessing(),
        _sample_agent_a_output(),
        _sample_segment_prep(),
        output_path,
        title="Saved Pairwise Report",
    )

    assert written == output_path
    content = output_path.read_text(encoding="utf-8")
    assert "Saved Pairwise Report" in content
    assert "<html" in content
