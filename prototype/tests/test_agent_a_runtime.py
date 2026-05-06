from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from google.genai import types

from v2t_prototype import create_gemini_client, upload_video_file
from v2t_prototype import (
    AgentAPostValidationError,
    AgentAResponseParseError,
    Cut,
    CutMapping,
    CutSourceMapping,
    Entity,
    EntityRegistry,
    FullVideoAssetResult,
    LocalPreprocessingResult,
    VideoMetadata,
    run_agent_a_runtime,
)
from v2t_prototype.agent_a_runtime import (
    DEFAULT_AGENT_A_SYSTEM_PROMPT,
    _parse_agent_a_response,
    validate_cut_mapping,
)
from v2t_prototype.gemini_client import GeminiGenerationParams, wait_for_uploaded_file_active


def make_full_video_asset_result() -> FullVideoAssetResult:
    return FullVideoAssetResult(
        local=LocalPreprocessingResult(
            video_metadata=VideoMetadata(
                video_path="videos/sample.mp4",
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
            video_path="videos/sample.mp4",
            video_mime_type="video/mp4",
        ),
        video_url="gs://bucket/sample.mp4",
        gemini_file_name="files/sample",
        upload_timestamp_utc="2026-04-14T00:00:00Z",
    )


def _write_dummy_video_file(path: Path) -> None:
    path.write_bytes(b"dummy-video-content")


def _valid_agent_a_response_payload() -> dict:
    return {
        "entities": [
            {
                "id": "baby",
                "label": "baby",
                "children": [
                    {
                        "id": "baby_footstep",
                        "label": "baby footstep",
                    }
                ],
            }
        ],
        "ambience": [],
        "unknowns": [],
        "cut_mapping": [
            {
                "cut_id": "CUT_001",
                "sfx_source_ids": ["baby_footstep"],
                "ambience_source_ids": [],
            },
            {
                "cut_id": "CUT_002",
                "sfx_source_ids": [],
                "ambience_source_ids": [],
            },
        ],
    }


def _valid_agent_a_response_json() -> str:
    return json.dumps(_valid_agent_a_response_payload())


def _usage_metadata(
    *,
    prompt_token_count: int = 0,
    candidates_token_count: int = 0,
    total_token_count: int | None = None,
):
    return SimpleNamespace(
        prompt_token_count=prompt_token_count,
        candidates_token_count=candidates_token_count,
        total_token_count=total_token_count if total_token_count is not None else prompt_token_count + candidates_token_count,
        cached_content_token_count=0,
        thoughts_token_count=0,
        tool_use_prompt_token_count=0,
    )


def _make_mock_client(response_text: str, *, usage_metadata=None) -> Mock:
    client = Mock()
    files_mock = Mock()
    files_mock.upload.return_value = SimpleNamespace(
        name="files/uploaded_video",
        uri="gs://bucket/uploaded_video.mp4",
        state=types.FileState.PROCESSING,
    )
    files_mock.get.return_value = SimpleNamespace(
        name="files/uploaded_video",
        uri="gs://bucket/uploaded_video.mp4",
        state=types.FileState.ACTIVE,
    )
    client.files = files_mock
    client.models.generate_content.return_value = SimpleNamespace(
        text=response_text,
        usage_metadata=usage_metadata or _usage_metadata(),
    )
    return client


def test_create_gemini_client_uses_genai_client_constructor():
    with patch("v2t_prototype.gemini_client.genai.Client") as mock_client_cls:
        create_gemini_client()
        create_gemini_client(api_key="test-key")

    assert mock_client_cls.call_count == 2
    assert mock_client_cls.call_args_list[0].kwargs == {}
    assert mock_client_cls.call_args_list[1].kwargs == {"api_key": "test-key"}


def test_upload_video_file_calls_files_api_upload(tmp_path: Path):
    video_path = tmp_path / "upload_input.mp4"
    _write_dummy_video_file(video_path)
    client = Mock()
    client.files.upload.return_value = SimpleNamespace(uri="gs://bucket/upload_input.mp4")

    uploaded_file = upload_video_file(client, video_path)

    assert uploaded_file.uri == "gs://bucket/upload_input.mp4"
    client.files.upload.assert_called_once_with(file=video_path)


def test_run_agent_a_runtime_success_with_structured_output_config():
    full_video_asset = make_full_video_asset_result()
    client = _make_mock_client(
        _valid_agent_a_response_json(),
        usage_metadata=_usage_metadata(prompt_token_count=1200, candidates_token_count=300),
    )

    output = run_agent_a_runtime(full_video_asset=full_video_asset, client=client)

    assert output.request.video_url == "gs://bucket/sample.mp4"
    assert output.response.entity_registry.entities[0].id == "baby"
    assert output.model == "gemini-2.5-pro"
    assert output.usage.total_token_count == 1500
    assert output.estimated_cost_usd == pytest.approx(0.0045)
    call_kwargs = client.models.generate_content.call_args.kwargs
    config = call_kwargs["config"]
    assert config.system_instruction == DEFAULT_AGENT_A_SYSTEM_PROMPT
    user_prompt = call_kwargs["contents"][1]
    assert "Analyze the attached full video" in user_prompt
    assert "produce one cut_mapping entry per cut" in user_prompt
    assert '"id": "CUT_001"' in user_prompt
    assert "You are Agent A" not in user_prompt
    response_schema = config.response_json_schema
    assert response_schema["required"] == ["entities", "ambience", "unknowns", "cut_mapping"]
    child_schema = response_schema["$defs"]["_AgentAEntityChildSchema"]
    assert "children" not in child_schema["properties"]
    video_part = call_kwargs["contents"][0]
    assert video_part.video_metadata.fps == 5.0


def test_run_agent_a_runtime_accepts_temperature():
    full_video_asset = make_full_video_asset_result()
    client = _make_mock_client(_valid_agent_a_response_json())

    run_agent_a_runtime(
        full_video_asset=full_video_asset,
        client=client,
        temperature=0.0,
    )

    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.temperature == 0.0


def test_run_agent_a_runtime_accepts_generation_params():
    full_video_asset = make_full_video_asset_result()
    client = _make_mock_client(_valid_agent_a_response_json())

    run_agent_a_runtime(
        full_video_asset=full_video_asset,
        client=client,
        generation_params=GeminiGenerationParams(
            temperature=0.2,
            top_p=0.8,
            top_k=32,
            seed=42,
            max_output_tokens=2048,
        ),
    )

    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.temperature == 0.2
    assert config.top_p == 0.8
    assert config.top_k == 32
    assert config.seed == 42
    assert config.max_output_tokens == 2048


def test_agent_a_prompt_includes_hierarchy_and_vocal_exclusion_rules():
    assert "Hierarchy is strictly 2 levels: Entity -> Entity_Child." in DEFAULT_AGENT_A_SYSTEM_PROMPT
    assert "Exclude all mouth/throat-produced vocalization sources" in DEFAULT_AGENT_A_SYSTEM_PROMPT
    assert "EntityRegistry:" in DEFAULT_AGENT_A_SYSTEM_PROMPT
    assert "Ambience:" in DEFAULT_AGENT_A_SYSTEM_PROMPT
    assert "Example 1 - Entity with acoustically distinct children:" in DEFAULT_AGENT_A_SYSTEM_PROMPT
    assert "After building the registry, produce a cut_mapping" in DEFAULT_AGENT_A_SYSTEM_PROMPT


def test_run_agent_a_runtime_raises_on_invalid_json_response():
    full_video_asset = make_full_video_asset_result()
    client = _make_mock_client("{not-json")

    with pytest.raises(AgentAResponseParseError):
        run_agent_a_runtime(full_video_asset=full_video_asset, client=client)


def test_run_agent_a_runtime_raises_on_schema_mismatch():
    full_video_asset = make_full_video_asset_result()
    client = _make_mock_client(json.dumps({"entities": "invalid-type"}))

    with pytest.raises(AgentAResponseParseError):
        run_agent_a_runtime(full_video_asset=full_video_asset, client=client)


def test_run_agent_a_runtime_rejects_ambience_with_children_during_parse():
    full_video_asset = make_full_video_asset_result()
    client = _make_mock_client(
        json.dumps(
            {
                "entities": [],
                "ambience": [
                    {
                        "id": "wind",
                        "label": "wind",
                        "children": [{"id": "banner_flag", "label": "banner flag"}],
                    }
                ],
                "unknowns": [],
                "cut_mapping": [],
            }
        )
    )

    with pytest.raises(AgentAResponseParseError):
        run_agent_a_runtime(full_video_asset=full_video_asset, client=client)


def test_run_agent_a_runtime_raises_on_post_validation_failure():
    full_video_asset = make_full_video_asset_result()
    client = _make_mock_client(
        json.dumps(
            {
                "entities": [{"id": "Baby", "label": "baby"}],
                "ambience": [],
                "unknowns": [],
                "cut_mapping": [],
            }
        )
    )

    with pytest.raises(AgentAPostValidationError) as exc_info:
        run_agent_a_runtime(full_video_asset=full_video_asset, client=client)
    assert "Entity: invalid snake_case ID 'Baby'" in exc_info.value.issues


def test_parse_agent_a_response_defaults_missing_cut_mapping_to_empty():
    payload = _valid_agent_a_response_payload()
    payload.pop("cut_mapping")
    output = _parse_agent_a_response(json.dumps(payload))

    assert output.cut_mapping.mappings == []


def test_validate_cut_mapping_accepts_valid_mapping_and_ignores_order():
    registry = EntityRegistry(
        entities=[
            Entity(
                id="fighter",
                label="fighter",
                children=[
                    {"id": "fighter_sword", "label": "fighter sword"},
                ],
            ),
            Entity(id="tree", label="tree"),
        ],
        ambience=[{"id": "wind", "label": "wind"}],
        unknowns=[],
    )
    cut_mapping = CutMapping(
        mappings=[
            CutSourceMapping(cut_id="CUT_002", sfx_source_ids=["tree"], ambience_source_ids=["wind"]),
            CutSourceMapping(cut_id="CUT_001", sfx_source_ids=["fighter_sword"], ambience_source_ids=[]),
        ]
    )

    issues = validate_cut_mapping(cut_mapping, registry, ["CUT_001", "CUT_002"])
    assert issues == []


def test_validate_cut_mapping_reports_missing_unexpected_and_duplicate_cuts():
    registry = EntityRegistry(entities=[Entity(id="tree", label="tree")], ambience=[], unknowns=[])
    cut_mapping = CutMapping(
        mappings=[
            CutSourceMapping(cut_id="CUT_001"),
            CutSourceMapping(cut_id="CUT_001"),
            CutSourceMapping(cut_id="CUT_999"),
        ]
    )

    issues = validate_cut_mapping(cut_mapping, registry, ["CUT_001", "CUT_002"])
    assert "cut_mapping: duplicate cut_id 'CUT_001'" in issues
    assert "cut_mapping: missing entry for cut_id 'CUT_002'" in issues
    assert "cut_mapping: unexpected cut_id 'CUT_999'" in issues


def test_validate_cut_mapping_reports_invalid_source_ids():
    registry = EntityRegistry(
        entities=[
            Entity(
                id="fighter",
                label="fighter",
                children=[
                    {"id": "fighter_sword", "label": "fighter sword"},
                ],
            ),
            Entity(id="tree", label="tree"),
        ],
        ambience=[{"id": "wind", "label": "wind"}],
        unknowns=[{"id": "unknown_1", "label": "unknown 1", "visual_description": "hidden object"}],
    )
    cut_mapping = CutMapping(
        mappings=[
            CutSourceMapping(
                cut_id="CUT_001",
                sfx_source_ids=["fighter", "unknown_1", "wind"],
                ambience_source_ids=["tree", "unknown_1"],
            )
        ]
    )

    issues = validate_cut_mapping(cut_mapping, registry, ["CUT_001"])
    assert "cut_mapping[CUT_001]: 'fighter' is not a valid sfx leaf target" in issues
    assert "cut_mapping[CUT_001]: 'unknown_1' is not a valid sfx leaf target" in issues
    assert "cut_mapping[CUT_001]: 'wind' is not a valid sfx leaf target" in issues
    assert "cut_mapping[CUT_001]: 'tree' is not a valid ambience target" in issues
    assert "cut_mapping[CUT_001]: 'unknown_1' is not a valid ambience target" in issues


def test_wait_for_uploaded_file_active_returns_immediately_for_active_file():
    client = Mock()
    already_active = SimpleNamespace(
        name="files/already_active",
        uri="gs://bucket/already_active.mp4",
        state=types.FileState.ACTIVE,
    )

    result = wait_for_uploaded_file_active(client, already_active)

    assert result is already_active
    client.files.get.assert_not_called()


def test_wait_for_uploaded_file_active_polls_until_active():
    client = Mock()
    uploaded = SimpleNamespace(
        name="files/uploading",
        uri="gs://bucket/uploading.mp4",
        state=types.FileState.PROCESSING,
    )
    client.files.get.side_effect = [
        SimpleNamespace(
            name="files/uploading",
            uri="gs://bucket/uploading.mp4",
            state=types.FileState.PROCESSING,
        ),
        SimpleNamespace(
            name="files/uploading",
            uri="gs://bucket/uploading.mp4",
            state=types.FileState.ACTIVE,
        ),
    ]

    result = wait_for_uploaded_file_active(
        client,
        uploaded,
        timeout_seconds=1.0,
        poll_interval_seconds=0.001,
    )

    assert result.state == types.FileState.ACTIVE
    assert client.files.get.call_count == 2


def test_wait_for_uploaded_file_active_times_out_when_never_active():
    client = Mock()
    uploaded = SimpleNamespace(
        name="files/uploading",
        uri="gs://bucket/uploading.mp4",
        state=types.FileState.PROCESSING,
    )
    client.files.get.return_value = uploaded

    with pytest.raises(TimeoutError):
        wait_for_uploaded_file_active(
            client,
            uploaded,
            timeout_seconds=0.01,
            poll_interval_seconds=0.001,
        )
