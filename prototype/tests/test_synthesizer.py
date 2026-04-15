from __future__ import annotations

from v2t_prototype.models import Action, OnsetEvent, UnknownResolution
from v2t_prototype.synthesizer import synthesize_tracks


def make_action(
    action_id: str,
    primary_source_id: str,
    interaction_type: str,
    sound_description: str,
    event: dict,
    *,
    cut_id: str = "CUT_001",
    unknown: bool = False,
    suggestion: str | None = None,
    suggested_entity_id: str | None = None,
) -> Action:
    resolution = None
    if unknown:
        resolution = UnknownResolution(
            suggestion=suggestion or "UNRESOLVED",
            reason="test",
            suggested_entity_id=suggested_entity_id,
        )

    return Action(
        action_id=action_id,
        cut_id=cut_id,
        primary_source_id=primary_source_id,
        unknown_resolution=resolution,
        interaction_type=interaction_type,
        sound_description=sound_description,
        observed_visual_description="desc",
        event=event,
        boundary_flag=False,
    )


class FakeTrackJudge:
    def __init__(self, groups_by_key: dict[tuple[str, str, str], list[list[str]]] | None = None):
        self.calls: list[tuple[str, str, str]] = []
        self.groups_by_key = groups_by_key or {}

    def judge_group(self, actions, source_id, interaction_type, event_type):
        key = (source_id, interaction_type, event_type)
        self.calls.append(key)
        action_by_id = {action.action_id: action for action in actions}
        group_ids = self.groups_by_key.get(key)
        if group_ids is None:
            return [list(actions)]
        return [[action_by_id[action_id] for action_id in group] for group in group_ids]


def test_synthesize_tracks_collects_unresolved_unknowns():
    result = synthesize_tracks(
        [
            make_action(
                "unknown_act",
                "UNKNOWN_OBJECT_CUT001_1",
                "sfx",
                "mysterious clang",
                {"type": "onset", "timestamp": 0.4},
                unknown=True,
            )
        ]
    )

    assert result.track_manifest.tracks == []
    assert len(result.unresolved_unknowns) == 1
    assert result.unresolved_unknowns[0].unknown_id == "UNKNOWN_OBJECT_CUT001_1"


def test_synthesize_tracks_reassigns_unknown_source_before_grouping():
    judge = FakeTrackJudge(
        {
            ("obj_001", "sfx", "onset"): [["known", "reassign"]],
        }
    )
    result = synthesize_tracks(
        [
            make_action("known", "obj_001", "sfx", "short metal tap", {"type": "onset", "timestamp": 0.1}),
            make_action(
                "reassign",
                "UNKNOWN_OBJECT_CUT001_1",
                "sfx",
                "longer metal tap",
                {"type": "onset", "timestamp": 0.3},
                unknown=True,
                suggestion="REASSIGN_TO_EXISTING",
                suggested_entity_id="obj_001",
            ),
        ],
        track_judge=judge,
    )

    assert len(result.track_manifest.tracks) == 1
    assert result.track_manifest.tracks[0].source_entity_id == "obj_001"
    assert len(result.track_manifest.tracks[0].events) == 2


def test_synthesize_tracks_marks_ambience_without_llm_grouping():
    judge = FakeTrackJudge()
    result = synthesize_tracks(
        [
            make_action(
                "amb1",
                "amb_001",
                "ambience",
                "room tone",
                {"type": "continuous", "start_time": 0.0, "end_time": 2.0},
            ),
            make_action(
                "amb2",
                "amb_001",
                "ambience",
                "steady room tone",
                {"type": "continuous", "start_time": 2.0, "end_time": 4.0},
            ),
        ],
        source_entity_kind_by_id={"amb_001": "AmbienceSource"},
        track_judge=judge,
    )

    assert judge.calls == []
    assert len(result.track_manifest.tracks) == 1
    assert result.track_manifest.tracks[0].track_id == "amb_001__ambience"
    assert result.track_manifest.tracks[0].track_type == "ambience"


def test_synthesize_tracks_splits_onset_and_continuous_before_track_judge():
    judge = FakeTrackJudge()
    result = synthesize_tracks(
        [
            make_action("a1", "obj_001", "sfx", "tap", {"type": "onset", "timestamp": 0.1}),
            make_action(
                "a2",
                "obj_001",
                "sfx",
                "rolling noise",
                {"type": "continuous", "start_time": 0.2, "end_time": 0.8},
            ),
        ],
        track_judge=judge,
    )

    assert ("obj_001", "sfx", "onset") in judge.calls
    assert ("obj_001", "sfx", "continuous") in judge.calls
    assert {track.track_id for track in result.track_manifest.tracks} == {
        "obj_001__sfx__onset",
        "obj_001__sfx__continuous",
    }


def test_synthesize_tracks_uses_desc_key_for_multi_group_sfx_ids():
    judge = FakeTrackJudge(
        {
            ("char_001", "sfx", "onset"): [["step_a"], ["step_b"]],
        }
    )
    result = synthesize_tracks(
        [
            make_action("step_a", "char_001", "sfx", "Soft sneaker step on tile", {"type": "onset", "timestamp": 0.1}),
            make_action("step_b", "char_001", "sfx", "Heavy boot step on gravel", {"type": "onset", "timestamp": 0.4}),
        ],
        track_judge=judge,
    )

    track_ids = {track.track_id for track in result.track_manifest.tracks}
    assert "char_001__sfx__onset__soft_sneaker_step_on_tile" in track_ids
    assert "char_001__sfx__onset__heavy_boot_step_on_gravel" in track_ids


def test_synthesize_tracks_adds_hash_suffix_when_desc_keys_collide():
    judge = FakeTrackJudge(
        {
            ("char_001", "sfx", "onset"): [["step_a"], ["step_b"]],
        }
    )
    result = synthesize_tracks(
        [
            make_action("step_a", "char_001", "sfx", "Footstep!!!", {"type": "onset", "timestamp": 0.1}),
            make_action("step_b", "char_001", "sfx", "Footstep???", {"type": "onset", "timestamp": 0.4}),
        ],
        track_judge=judge,
    )

    track_ids = [track.track_id for track in result.track_manifest.tracks]
    assert len(track_ids) == 2
    assert track_ids[0] != track_ids[1]
    assert all(track_id.startswith("char_001__sfx__onset__footstep") for track_id in track_ids)


def test_synthesize_tracks_sorts_events_within_a_group():
    judge = FakeTrackJudge(
        {
            ("obj_001", "sfx", "onset"): [["late", "early"]],
        }
    )
    result = synthesize_tracks(
        [
            make_action("late", "obj_001", "sfx", "late hit", {"type": "onset", "timestamp": 0.7}),
            make_action("early", "obj_001", "sfx", "early hit", {"type": "onset", "timestamp": 0.2}),
        ],
        track_judge=judge,
    )

    track = result.track_manifest.tracks[0]
    assert [event.timestamp for event in track.events] == [0.2, 0.7]
    assert track.sound_description == "early hit"


def test_synthesize_tracks_uses_single_action_groups_without_track_judge():
    result = synthesize_tracks(
        [
            make_action("a1", "obj_001", "sfx", "first hit", {"type": "onset", "timestamp": 0.1}),
            make_action("a2", "obj_001", "sfx", "second hit", {"type": "onset", "timestamp": 0.2}),
        ]
    )

    assert len(result.track_manifest.tracks) == 2
    assert {track.track_id for track in result.track_manifest.tracks} == {
        "obj_001__sfx__onset__first_hit",
        "obj_001__sfx__onset__second_hit",
    }
