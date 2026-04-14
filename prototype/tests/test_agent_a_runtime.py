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
    PreprocessingResult,
    run_agent_a_runtime,
)
from v2t_prototype.gemini_client import wait_for_uploaded_file_active


def make_preprocessing_result() -> PreprocessingResult:
    return PreprocessingResult.model_validate(
        {
            "video_metadata": {
                "video_path": "videos/sample.mp4",
                "fps": 24.0,
                "frame_count": 240,
                "duration_seconds": 10.0,
                "width": 1280,
                "height": 720,
            },
            "video_url": "gs://bucket/sample.mp4",
            "video_mime_type": "video/mp4",
            "cuts": [
                {"id": "CUT_001", "start_time": 0.0, "end_time": 4.0},
                {"id": "CUT_002", "start_time": 4.0, "end_time": 10.0},
            ],
        }
    )


def _write_dummy_video_file(path: Path) -> None:
    path.write_bytes(b"dummy-video-content")


def _valid_agent_a_response_json() -> str:
    return json.dumps(
        {
            "entity_registry": {
                "characters": [
                    {
                        "id": "char_001",
                        "label": "Player",
                        "visual_description": "table tennis player",
                        "entry_exit_intervals": [{"start_time": 0.0, "end_time": 10.0}],
                        "audibility": "likely_audible",
                    }
                ],
                "key_objects": [],
                "ambience_sources": [],
            }
        }
    )


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
    first_call_kwargs = mock_client_cls.call_args_list[0].kwargs
    second_call_kwargs = mock_client_cls.call_args_list[1].kwargs
    assert first_call_kwargs == {}
    assert second_call_kwargs == {"api_key": "test-key"}


def test_upload_video_file_calls_files_api_upload(tmp_path: Path):
    video_path = tmp_path / "upload_input.mp4"
    _write_dummy_video_file(video_path)
    client = Mock()
    client.files.upload.return_value = SimpleNamespace(uri="gs://bucket/upload_input.mp4")

    uploaded_file = upload_video_file(client, video_path)

    assert uploaded_file.uri == "gs://bucket/upload_input.mp4"
    client.files.upload.assert_called_once_with(file=video_path)


def test_run_agent_a_runtime_success_with_structured_output_config():
    preprocessing = make_preprocessing_result()
    client = _make_mock_client(
        _valid_agent_a_response_json(),
        usage_metadata=_usage_metadata(prompt_token_count=1200, candidates_token_count=300),
    )

    with patch("v2t_prototype.agent_a_runtime.types.Part.from_uri") as part_from_uri:
        part_from_uri.return_value = SimpleNamespace(content="video part")
        output = run_agent_a_runtime(preprocessing=preprocessing, client=client)

    assert output.request.video_url == "gs://bucket/sample.mp4"
    assert output.response.entity_registry.characters[0].id == "char_001"
    assert json.loads(output.model_dump_json())["request"]["video_url"] == "gs://bucket/sample.mp4"
    assert output.model == "gemini-2.5-pro"
    assert output.latency_ms >= 0.0
    assert output.usage.prompt_token_count == 1200
    assert output.usage.candidates_token_count == 300
    assert output.usage.total_token_count == 1500
    assert output.estimated_cost_usd == pytest.approx(0.0045)
    client.files.upload.assert_not_called()
    client.files.get.assert_not_called()
    part_from_uri.assert_called_once_with(
        file_uri=preprocessing.video_url,
        mime_type="video/mp4",
    )
    generate_kwargs = client.models.generate_content.call_args.kwargs
    assert generate_kwargs["model"] == "gemini-2.5-pro"
    assert generate_kwargs["config"].response_mime_type == "application/json"
    assert "properties" in generate_kwargs["config"].response_json_schema


def test_run_agent_a_runtime_raises_on_invalid_json_response():
    preprocessing = make_preprocessing_result()
    client = _make_mock_client("{not-json")

    with pytest.raises(AgentAResponseParseError):
        run_agent_a_runtime(preprocessing=preprocessing, client=client)


def test_run_agent_a_runtime_raises_on_schema_mismatch():
    preprocessing = make_preprocessing_result()
    client = _make_mock_client(
        json.dumps(
            {
                "entity_registry": "invalid-type",
            }
        )
    )

    with pytest.raises(AgentAResponseParseError):
        run_agent_a_runtime(preprocessing=preprocessing, client=client)


def test_run_agent_a_runtime_raises_on_post_validation_failure():
    preprocessing = make_preprocessing_result()
    client = _make_mock_client(
        json.dumps(
            {
                "entity_registry": {
                    "characters": [
                        {
                            "id": "person_001",
                            "label": "Player",
                            "visual_description": "table tennis player",
                            "entry_exit_intervals": [{"start_time": 0.0, "end_time": 10.0}],
                            "audibility": "likely_audible",
                        }
                    ],
                    "key_objects": [],
                    "ambience_sources": [],
                }
            }
        )
    )

    with pytest.raises(AgentAPostValidationError) as exc_info:
        run_agent_a_runtime(preprocessing=preprocessing, client=client)
    assert "invalid character id prefix person_001" in exc_info.value.issues



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
        name="files/stuck_processing",
        uri="gs://bucket/stuck_processing.mp4",
        state=types.FileState.PROCESSING,
    )
    client.files.get.return_value = SimpleNamespace(
        name="files/stuck_processing",
        uri="gs://bucket/stuck_processing.mp4",
        state=types.FileState.PROCESSING,
    )

    with pytest.raises(TimeoutError):
        wait_for_uploaded_file_active(
            client,
            uploaded,
            timeout_seconds=0.01,
            poll_interval_seconds=0.005,
        )
