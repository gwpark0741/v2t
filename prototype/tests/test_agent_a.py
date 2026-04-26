from v2t_prototype import (
    AgentAResponse,
    Cut,
    FullVideoAssetResult,
    LocalPreprocessingResult,
    VideoMetadata,
    build_agent_a_request,
    validate_agent_a_response,
)


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


def test_build_agent_a_request_from_full_video_asset_result():
    full_video_asset = make_full_video_asset_result()

    request = build_agent_a_request(full_video_asset=full_video_asset)

    assert request.video_url == "gs://bucket/sample.mp4"
    assert request.video_mime_type == "video/mp4"
    assert request.video_metadata.video_path == "videos/sample.mp4"
    assert [cut.id for cut in request.cuts] == ["CUT_001", "CUT_002"]


def test_validate_agent_a_response_accepts_valid_registry():
    full_video_asset = make_full_video_asset_result()
    response = AgentAResponse(
        entity_registry={
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
            "ambience": [{"id": "wind", "label": "wind"}],
            "unknowns": [
                {
                    "id": "unknown_1",
                    "label": "unknown 1",
                    "visual_description": "wrapped object near the samurai's back",
                }
            ],
        }
    )

    issues = validate_agent_a_response(full_video_asset, response)
    assert issues == []


def test_validate_agent_a_response_reports_structure_issues():
    full_video_asset = make_full_video_asset_result()
    response = AgentAResponse(
        entity_registry={
            "entities": [
                {
                    "id": "Baby",
                    "label": "baby",
                    "children": [{"id": "baby_footstep", "label": "baby footstep"}],
                }
            ],
            "ambience": [{"id": "wind", "label": "wind"}],
            "unknowns": [
                {
                    "id": "mystery_1",
                    "label": "unknown 1",
                    "visual_description": "wrapped object",
                }
            ],
        }
    )

    issues = validate_agent_a_response(full_video_asset, response)
    assert "Entity: invalid snake_case ID 'Baby'" in issues
    assert "Unknown: ID must match unknown_N pattern, got 'mystery_1'" in issues


def test_validate_agent_a_response_rejects_duplicate_ids():
    full_video_asset = make_full_video_asset_result()
    response = AgentAResponse(
        entity_registry={
            "entities": [
                {
                    "id": "samurai",
                    "label": "samurai",
                    "children": [{"id": "samurai_armor", "label": "samurai armor"}],
                }
            ],
            "ambience": [{"id": "samurai", "label": "duplicate samurai"}],
            "unknowns": [],
        }
    )

    issues = validate_agent_a_response(full_video_asset, response)
    assert "Ambience: duplicate ID 'samurai'" in issues


def test_validate_agent_a_response_rejects_duplicate_child_id():
    full_video_asset = make_full_video_asset_result()
    response = AgentAResponse(
        entity_registry={
            "entities": [
                {
                    "id": "samurai",
                    "label": "samurai",
                    "children": [{"id": "shared_item", "label": "shared item"}],
                },
                {
                    "id": "guard",
                    "label": "guard",
                    "children": [{"id": "shared_item", "label": "shared item"}],
                },
            ],
            "ambience": [],
            "unknowns": [],
        }
    )

    issues = validate_agent_a_response(full_video_asset, response)
    assert "Child of guard: duplicate ID 'shared_item'" in issues
