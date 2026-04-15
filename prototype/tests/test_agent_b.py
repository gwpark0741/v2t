from __future__ import annotations

from v2t_prototype.agent_b import build_agent_b_cut_input, validate_agent_b_response
from v2t_prototype.models import (
    Action,
    AgentBResponse,
    AmbienceSource,
    Character,
    ContinuousEvent,
    Cut,
    EntityRegistry,
    Interval,
    KeyObject,
    SegmentClip,
    UnknownResolution,
)


def _entity_registry() -> EntityRegistry:
    return EntityRegistry(
        characters=[
            Character(
                id="char_001",
                label="Fighter",
                visual_description="Armored fighter",
                entry_exit_intervals=[Interval(start_time=0.0, end_time=10.0)],
                audibility="likely_audible",
            )
        ],
        key_objects=[
            KeyObject(
                id="obj_001",
                label="Sword",
                visual_description="Steel sword",
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
                space_description="Outdoor arena",
                distance_profile="mid",
                tonal_quality="dry",
            )
        ],
    )


def _segment_clip() -> SegmentClip:
    return SegmentClip(
        cut_id="CUT_001",
        local_clip_path="/tmp/CUT_001.mp4",
        clip_video_url="https://example.com/cut001",
        clip_gemini_file_name="files/cut001",
        clip_video_mime_type="video/mp4",
    )


def _cut() -> Cut:
    return Cut(id="CUT_001", start_time=0.0, end_time=4.0)


def _response(*actions: Action) -> AgentBResponse:
    return AgentBResponse(actions=list(actions))


def _valid_action(**overrides) -> Action:
    payload = {
        "action_id": "act_CUT_001_001",
        "cut_id": "CUT_001",
        "primary_source_id": "obj_001",
        "interaction_type": "sfx",
        "sound_description": "metal sword clash with a bright ring",
        "observed_visual_description": "two swords collide",
        "event": ContinuousEvent(type="continuous", start_time=0.5, end_time=1.5),
        "boundary_flag": False,
    }
    payload.update(overrides)
    return Action.model_validate(payload)


def test_build_agent_b_cut_input_success():
    result = build_agent_b_cut_input(_segment_clip(), _cut(), _entity_registry())

    assert result.cut_id == "CUT_001"
    assert result.cut_start_time == 0.0
    assert result.cut_end_time == 4.0
    assert result.clip_video_url == "https://example.com/cut001"


def test_build_agent_b_cut_input_raises_on_cut_id_mismatch():
    clip = _segment_clip().model_copy(update={"cut_id": "CUT_002"})

    try:
        build_agent_b_cut_input(clip, _cut(), _entity_registry())
    except ValueError as exc:
        assert "clip.cut_id" in str(exc)
    else:
        raise AssertionError("Expected ValueError for mismatched cut_id")


def test_validate_agent_b_response_accepts_valid_response():
    issues = validate_agent_b_response(
        _response(_valid_action()),
        "CUT_001",
        _entity_registry(),
        cut_start_time=0.0,
        cut_end_time=4.0,
    )

    assert issues == []


def test_validate_agent_b_response_reports_duplicate_action_id():
    issues = validate_agent_b_response(
        _response(
            _valid_action(action_id="act_CUT_001_001"),
            _valid_action(action_id="act_CUT_001_001"),
        ),
        "CUT_001",
        _entity_registry(),
        cut_start_time=0.0,
        cut_end_time=4.0,
    )

    assert issues == ["AGENT_B_DUPLICATE_ACTION_ID"]


def test_validate_agent_b_response_reports_cut_id_mismatch_and_unknown_source():
    issues = validate_agent_b_response(
        _response(
            _valid_action(
                cut_id="CUT_999",
                primary_source_id="obj_missing",
            )
        ),
        "CUT_001",
        _entity_registry(),
        cut_start_time=0.0,
        cut_end_time=4.0,
    )

    assert issues == [
        "AGENT_B_CUT_ID_MISMATCH",
        "AGENT_B_UNKNOWN_SOURCE_ID",
    ]


def test_validate_agent_b_response_reports_invalid_unknown_format():
    issues = validate_agent_b_response(
        _response(
            _valid_action(
                primary_source_id="UNKNOWN_BAD",
                unknown_resolution=UnknownResolution(
                    suggestion="UNRESOLVED",
                    reason="unclear source",
                ),
            )
        ),
        "CUT_001",
        _entity_registry(),
        cut_start_time=0.0,
        cut_end_time=4.0,
    )

    assert issues == ["AGENT_B_INVALID_UNKNOWN_FORMAT"]


def test_validate_agent_b_response_reports_invalid_reassign_target():
    issues = validate_agent_b_response(
        _response(
            _valid_action(
                primary_source_id="UNKNOWN_OBJECT_CUT001_1",
                unknown_resolution=UnknownResolution(
                    suggestion="REASSIGN_TO_EXISTING",
                    suggested_entity_id="obj_missing",
                    reason="looks like a sword",
                ),
            )
        ),
        "CUT_001",
        _entity_registry(),
        cut_start_time=0.0,
        cut_end_time=4.0,
    )

    assert issues == ["AGENT_B_INVALID_REASSIGN_TARGET"]


def test_validate_agent_b_response_reports_event_time_out_of_local_range_for_onset():
    issues = validate_agent_b_response(
        _response(
            _valid_action(
                event={"type": "onset", "timestamp": 4.5},
            )
        ),
        "CUT_001",
        _entity_registry(),
        cut_start_time=0.0,
        cut_end_time=4.0,
    )

    assert issues == ["AGENT_B_EVENT_TIME_OUT_OF_LOCAL_RANGE"]


def test_validate_agent_b_response_reports_event_time_out_of_local_range_for_continuous():
    issues = validate_agent_b_response(
        _response(
            _valid_action(
                event={"type": "continuous", "start_time": 3.5, "end_time": 4.2},
            )
        ),
        "CUT_001",
        _entity_registry(),
        cut_start_time=0.0,
        cut_end_time=4.0,
    )

    assert issues == ["AGENT_B_EVENT_TIME_OUT_OF_LOCAL_RANGE"]


def test_validate_agent_b_response_accepts_continuous_event_at_rounded_cut_boundary():
    issues = validate_agent_b_response(
        _response(
            _valid_action(
                event={"type": "continuous", "start_time": 0.0, "end_time": 1.433},
            )
        ),
        "CUT_001",
        _entity_registry(),
        cut_start_time=5.0,
        cut_end_time=6.433,
    )

    assert issues == []
