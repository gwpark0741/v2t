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

    request = build_agent_a_request(
        full_video_asset=full_video_asset,
    )

    assert request.video_url == "gs://bucket/sample.mp4"
    assert request.video_mime_type == "video/mp4"
    assert request.video_metadata.video_path == "videos/sample.mp4"
    assert [cut.id for cut in request.cuts] == ["CUT_001", "CUT_002"]


def test_validate_agent_a_response_accepts_valid_registry():
    full_video_asset = make_full_video_asset_result()
    response = AgentAResponse.model_validate(
        {
            "entity_registry": {
                "characters": [
                    {
                        "id": "char_001",
                        "label": "Player",
                        "visual_description": "table tennis player",
                        "entry_exit_intervals": [
                            {"start_time": 0.0, "end_time": 10.0}
                        ],
                        "audibility": "likely_audible",
                    }
                ],
                "key_objects": [
                    {
                        "id": "obj_001",
                        "label": "Paddle",
                        "visual_description": "red paddle",
                        "material": "wood",
                        "surface": "rubber",
                        "has_mechanism": False,
                        "audibility": "audible",
                    }
                ],
                "ambience_sources": [
                    {
                        "id": "amb_001",
                        "label": "Gym Room Tone",
                        "space_description": "indoor sports gym",
                        "distance_profile": "mid",
                        "tonal_quality": "bright",
                    }
                ],
            }
        }
    )

    issues = validate_agent_a_response(full_video_asset, response)
    assert issues == []


def test_validate_agent_a_response_reports_entity_id_issues():
    full_video_asset = make_full_video_asset_result()
    response = AgentAResponse.model_validate(
        {
            "entity_registry": {
                "characters": [
                    {
                        "id": "person_001",
                        "label": "Player",
                        "visual_description": "table tennis player",
                        "entry_exit_intervals": [
                            {"start_time": 0.0, "end_time": 10.0}
                        ],
                        "audibility": "audible",
                    }
                ],
                "key_objects": [
                    {
                        "id": "person_001",
                        "label": "Paddle",
                        "visual_description": "red paddle",
                        "material": "wood",
                        "surface": "rubber",
                        "has_mechanism": False,
                        "audibility": "audible",
                    }
                ],
                "ambience_sources": [
                    {
                        "id": "ambience_001",
                        "label": "Gym Room Tone",
                        "space_description": "indoor sports gym",
                        "distance_profile": "mid",
                        "tonal_quality": "bright",
                    }
                ],
            }
        }
    )

    issues = validate_agent_a_response(full_video_asset, response)
    assert "invalid character id prefix person_001" in issues
    assert "duplicate entity id person_001" in issues
    assert "invalid ambience source id prefix ambience_001" in issues
