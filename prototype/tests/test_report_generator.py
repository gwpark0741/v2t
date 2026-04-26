from __future__ import annotations

import json
from pathlib import Path

from v2t_prototype.report_generator import generate_pipeline_report


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _build_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run_demo"
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"fake")
    clip_path = run_dir / "stage_04_segment_prep" / "clips" / "CUT_001.mp4"
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    clip_path.write_bytes(b"clip")

    _write_json(
        run_dir / "run_manifest.json",
        {
            "run_id": "run_demo",
            "video_path": str(source_video),
            "created_at_utc": "2026-04-14T00:00:00Z",
            "entry_stage": None,
            "stages": [],
        },
    )
    _write_json(
        run_dir / "stage_01_local_preprocessing" / "output.json",
        {
            "video_metadata": {
                "video_path": str(source_video),
                "fps": 30.0,
                "frame_count": 150,
                "duration_seconds": 5.0,
                "width": 1280,
                "height": 720,
            },
            "cuts": [{"id": "CUT_001", "start_time": 0.0, "end_time": 5.0}],
            "video_path": str(source_video),
            "video_mime_type": "video/mp4",
        },
    )
    _write_json(
        run_dir / "stage_01_local_preprocessing" / "warnings.json",
        {"stage": "stage_01_local_preprocessing", "generated_at_utc": "2026-04-14T00:00:00Z", "warnings": []},
    )
    _write_json(
        run_dir / "stage_02_full_video_asset" / "output.json",
        {
            "local": {
                "video_metadata": {
                    "video_path": str(source_video),
                    "fps": 30.0,
                    "frame_count": 150,
                    "duration_seconds": 5.0,
                    "width": 1280,
                    "height": 720,
                },
                "cuts": [{"id": "CUT_001", "start_time": 0.0, "end_time": 5.0}],
                "video_path": str(source_video),
                "video_mime_type": "video/mp4",
            },
            "video_url": "gs://bucket/video.mp4",
            "gemini_file_name": "files/123",
            "upload_timestamp_utc": "2026-04-14T00:00:01Z",
        },
    )
    _write_json(
        run_dir / "stage_02_full_video_asset" / "warnings.json",
        {"stage": "stage_02_full_video_asset", "generated_at_utc": "2026-04-14T00:00:01Z", "warnings": []},
    )
    _write_json(
        run_dir / "stage_03_agent_a" / "output.json",
        {
            "request": {},
            "raw_response_text": "{}",
            "response": {
                "entity_registry": {
                    "entities": [
                        {
                            "id": "player",
                            "label": "Player",
                            "children": [
                                {
                                    "id": "char_001",
                                    "label": "Player cloth",
                                }
                            ],
                        },
                        {
                            "id": "ball",
                            "label": "Ball",
                            "children": [
                                {
                                    "id": "obj_001",
                                    "label": "Ball impact",
                                }
                            ],
                        }
                    ],
                    "ambience": [
                        {
                            "id": "amb_001",
                            "label": "Hall",
                        }
                    ],
                    "unknowns": [],
                }
            },
        },
    )
    _write_json(
        run_dir / "stage_03_agent_a" / "warnings.json",
        {"stage": "stage_03_agent_a", "generated_at_utc": "2026-04-14T00:00:02Z", "warnings": []},
    )
    _write_json(
        run_dir / "stage_04_segment_prep" / "output.json",
        {
            "clips": [
                {
                    "cut_id": "CUT_001",
                    "local_clip_path": str(clip_path),
                    "clip_video_url": "gs://bucket/clip.mp4",
                    "clip_gemini_file_name": "files/clip",
                    "clip_video_mime_type": "video/mp4",
                }
            ],
            "skipped_cuts": [],
            "warnings": [],
        },
    )
    _write_json(
        run_dir / "stage_04_segment_prep" / "warnings.json",
        {"stage": "stage_04_segment_prep", "generated_at_utc": "2026-04-14T00:00:03Z", "warnings": []},
    )
    _write_json(
        run_dir / "stage_05_agent_b" / "output.json",
        {
            "cut_outputs": [
                {
                    "cut_id": "CUT_001",
                    "model": "gemini-2.5-pro",
                    "raw_response_text": "{}",
                    "validation_issues": [],
                    "actions": [
                        {
                            "action_id": "act_CUT_001_001",
                            "cut_id": "CUT_001",
                            "primary_source_id": "obj_001",
                            "interaction_type": "sfx",
                            "sound_description": "Ping pong bounce on a wooden table",
                            "observed_visual_description": "Ball hits table",
                            "boundary_flag": False,
                            "event": {"type": "onset", "timestamp": 1.2},
                            "unknown_resolution": None,
                        }
                    ],
                }
            ],
            "skipped_cut_ids": [],
            "failed_cut_ids": [],
            "total_actions": 1,
            "unresolved_count": 0,
            "reassigned_count": 0,
            "warnings": [],
        },
    )
    _write_json(
        run_dir / "stage_05_agent_b" / "warnings.json",
        {"stage": "stage_05_agent_b", "generated_at_utc": "2026-04-14T00:00:04Z", "warnings": []},
    )
    _write_json(
        run_dir / "stage_06_agent_c" / "output.json",
        {
            "pipeline_result": {
                "track_manifest": {
                    "tracks": [
                        {
                            "track_number": 1,
                            "track_id": "obj_001__sfx__onset",
                            "track_type": "sfx",
                            "source_entity_id": "obj_001",
                            "sound_description": "Ping pong bounce on a wooden table",
                            "events": [{"type": "onset", "timestamp": 1.2}],
                        }
                    ]
                },
                "unresolved_unknowns": [],
                "warnings": [],
            },
            "track_group_judgments": [
                {
                    "group_key": "obj_001__sfx__onset",
                    "input_action_ids": ["act_CUT_001_001"],
                    "output_groups": [
                        {
                            "action_ids": ["act_CUT_001_001"],
                            "reason": "single bounce event",
                        }
                    ],
                    "source": "single_action",
                    "model": "gemini-2.5-flash",
                }
            ],
            "merge_group_count": 1,
            "llm_call_count": 0,
            "total_llm_latency_ms": 0.0,
            "per_call_llm_latency_ms": [],
            "llm_usage": {"prompt_token_count": 0, "candidates_token_count": 0, "total_token_count": 0},
            "estimated_llm_cost_usd": 0.0,
        },
    )
    _write_json(
        run_dir / "stage_06_agent_c" / "warnings.json",
        {"stage": "stage_06_agent_c", "generated_at_utc": "2026-04-14T00:00:05Z", "warnings": []},
    )
    return run_dir


def test_generate_pipeline_report_writes_combined_html(tmp_path: Path):
    run_dir = _build_run_dir(tmp_path)

    output_path = generate_pipeline_report(run_dir)

    assert output_path == run_dir / "pipeline_report.html"
    html = output_path.read_text(encoding="utf-8")
    assert "Pipeline Report" in html
    assert "01 Cuts" in html
    assert "02 Upload" in html
    assert "03 Agent A" in html
    assert "04 Segments" in html
    assert "05 Agent B" in html
    assert "06 Tracks" in html
    assert "files/123" in html
    assert "act_CUT_001_001" in html
    assert "obj_001__sfx__onset" in html
    assert "single_action" in html
    assert "data-tab-target='cuts'" in html
    assert "pipeline-report-source-video" in html


def test_generate_pipeline_report_renders_missing_stage_fallback(tmp_path: Path):
    run_dir = _build_run_dir(tmp_path)
    (run_dir / "stage_05_agent_b" / "output.json").unlink()

    html = generate_pipeline_report(run_dir).read_text(encoding="utf-8")

    assert "Stage 05 artifact not found" in html


def test_generate_pipeline_report_sorts_tracks_and_shows_description(tmp_path: Path):
    run_dir = _build_run_dir(tmp_path)
    _write_json(
        run_dir / "stage_06_agent_c" / "output.json",
        {
            "pipeline_result": {
                "track_manifest": {
                    "tracks": [
                        {
                            "track_number": 1,
                            "track_id": "char_001__sfx__continuous",
                            "track_type": "sfx",
                            "source_entity_id": "char_001",
                            "sound_description": "Cloth rustle.",
                            "events": [{"type": "continuous", "start_time": 1.0, "end_time": 2.0}],
                        },
                        {
                            "track_number": 2,
                            "track_id": "obj_001__sfx__onset",
                            "track_type": "sfx",
                            "source_entity_id": "obj_001",
                            "sound_description": "Ping pong bounce.",
                            "events": [{"type": "onset", "timestamp": 1.2}],
                        },
                        {
                            "track_number": 3,
                            "track_id": "amb_001__ambience",
                            "track_type": "ambience",
                            "source_entity_id": "amb_001",
                            "sound_description": "Room tone.",
                            "events": [{"type": "continuous", "start_time": 0.0, "end_time": 5.0}],
                        },
                    ]
                },
                "unresolved_unknowns": [],
                "warnings": [],
            },
            "track_group_judgments": [],
            "merge_group_count": 3,
            "llm_call_count": 0,
            "total_llm_latency_ms": 0.0,
            "per_call_llm_latency_ms": [],
            "llm_usage": {"prompt_token_count": 0, "candidates_token_count": 0, "total_token_count": 0},
            "estimated_llm_cost_usd": 0.0,
        },
    )

    html = generate_pipeline_report(run_dir).read_text(encoding="utf-8")

    char_source_index = html.index("<span class='mono truncate'>player - char_001</span><span class='badge badge-info'>1 tracks</span>")
    obj_source_index = html.index("<span class='mono truncate'>ball - obj_001</span><span class='badge badge-info'>1 tracks</span>")
    amb_source_index = html.index("<span class='mono truncate'>amb_001</span><span class='badge badge-info'>1 tracks</span>")
    assert char_source_index < obj_source_index < amb_source_index
    cloth_index = html.index("Cloth rustle.")
    bounce_index = html.index("Ping pong bounce.")
    ambience_index = html.index("Room tone.")
    assert cloth_index < bounce_index < ambience_index
    assert "All Tracks" in html
    assert "By Source" in html
    assert "tracks-layout" in html
    assert "tracks-scroll-panel" in html
    assert "Source Video" in html
    assert ">sfx<" in html
    assert ">continuous<" in html
    assert ">onset<" in html
    assert "Track 01" in html
    assert "Track 02" in html
    assert "Track 03" in html
    assert "char_001__sfx__continuous" in html
    assert "player - char_001" in html
    assert "ball - obj_001" in html
    assert "continuous @ 1.000s ~ 2.000s" not in html


def test_generate_pipeline_report_uses_track_numbers_from_stage_06_output(tmp_path: Path):
    run_dir = _build_run_dir(tmp_path)
    _write_json(
        run_dir / "stage_06_agent_c" / "output.json",
        {
            "pipeline_result": {
                "track_manifest": {
                    "tracks": [
                        {
                            "track_number": 7,
                            "track_id": "char_001__sfx__onset",
                            "track_type": "sfx",
                            "source_entity_id": "char_001",
                            "sound_description": "Legacy bounce",
                            "events": [{"type": "onset", "timestamp": 1.2}],
                        }
                    ]
                },
                "unresolved_unknowns": [],
                "warnings": [],
            },
            "track_group_judgments": [
                {
                    "group_key": "char_001__sfx__onset",
                    "input_action_ids": ["act_CUT_001_001"],
                    "output_groups": [{"action_ids": ["act_CUT_001_001"], "reason": "single action"}],
                    "reason": "Same normalized surface.",
                    "source": "single_action",
                    "model": "gemini-2.5-flash",
                }
            ],
            "merge_group_count": 1,
            "llm_call_count": 1,
            "total_llm_latency_ms": 3.5,
            "per_call_llm_latency_ms": [3.5],
            "llm_usage": {"prompt_token_count": 0, "candidates_token_count": 0, "total_token_count": 0},
            "estimated_llm_cost_usd": 0.0,
        },
    )

    html = generate_pipeline_report(run_dir).read_text(encoding="utf-8")

    assert "Track 07" in html
    assert "char_001__sfx__onset" in html
    assert "player - char_001" in html
    assert ">sfx<" in html
    assert "Legacy bounce" in html
    assert "Track Group Judgments" in html
