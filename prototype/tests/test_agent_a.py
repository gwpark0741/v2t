from v2t_prototype import AgentAResponse, PreprocessingResult, build_agent_a_request, validate_agent_a_response


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


def test_build_agent_a_request_from_preprocessing_and_video_url():
    preprocessing = make_preprocessing_result()

    request = build_agent_a_request(
        preprocessing=preprocessing,
    )

    assert request.video_url == "gs://bucket/sample.mp4"
    assert request.video_mime_type == "video/mp4"
    assert request.video_metadata.video_path == "videos/sample.mp4"
    assert [cut.id for cut in request.cuts] == ["CUT_001", "CUT_002"]


def test_validate_agent_a_response_accepts_valid_registry_and_cut_coverage():
    preprocessing = make_preprocessing_result()
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
            },
            "cut_enrichments": [
                {
                    "cut_id": "CUT_001",
                    "camera_angle": "medium shot",
                    "transition_type": "hard cut",
                    "camera_notes": "player prepares",
                },
                {
                    "cut_id": "CUT_002",
                    "camera_angle": "close up",
                    "transition_type": "hard cut",
                    "camera_notes": "impact focus",
                },
            ],
        }
    )

    issues = validate_agent_a_response(preprocessing, response)
    assert issues == []


def test_validate_agent_a_response_reports_cut_linkage_issues():
    preprocessing = make_preprocessing_result()
    response = AgentAResponse.model_validate(
        {
            "entity_registry": {
                "characters": [],
                "key_objects": [],
                "ambience_sources": [],
            },
            "cut_enrichments": [
                {
                    "cut_id": "CUT_001",
                    "camera_angle": "medium shot",
                    "transition_type": "hard cut",
                    "camera_notes": "first mention",
                },
                {
                    "cut_id": "CUT_001",
                    "camera_angle": "wide shot",
                    "transition_type": "hard cut",
                    "camera_notes": "duplicate mention",
                },
                {
                    "cut_id": "CUT_999",
                    "camera_angle": "wide shot",
                    "transition_type": "hard cut",
                    "camera_notes": "nonexistent cut",
                },
            ],
        }
    )

    issues = validate_agent_a_response(preprocessing, response)
    assert "duplicate cut enrichment CUT_001" in issues
    assert "unknown cut_id in enrichment CUT_999" in issues
    assert "missing cut enrichment for CUT_002" in issues


def test_validate_agent_a_response_reports_entity_id_issues():
    preprocessing = make_preprocessing_result()
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
            },
            "cut_enrichments": [
                {
                    "cut_id": "CUT_001",
                    "camera_angle": "medium shot",
                    "transition_type": "hard cut",
                    "camera_notes": "player prepares",
                },
                {
                    "cut_id": "CUT_002",
                    "camera_angle": "close up",
                    "transition_type": "hard cut",
                    "camera_notes": "impact focus",
                },
            ],
        }
    )

    issues = validate_agent_a_response(preprocessing, response)
    assert "invalid character id prefix person_001" in issues
    assert "duplicate entity id person_001" in issues
    assert "invalid ambience source id prefix ambience_001" in issues
