from __future__ import annotations

from v2t_prototype.models import Action, OnsetEvent, TrackGroupResult, UnknownResolution
from v2t_prototype.synthesizer import synthesize_tracks
from v2t_prototype.track_judge import TrackJudge, TrackJudgeDecision


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
        self.judgments = []

    def judge_group(self, actions, source_id, interaction_type, event_type):
        key = (source_id, interaction_type, event_type)
        self.calls.append(key)
        action_by_id = {action.action_id: action for action in actions}
        group_ids = self.groups_by_key.get(key)
        if group_ids is None:
            return [list(actions)]
        return [[action_by_id[action_id] for action_id in group] for group in group_ids]

    def judge_group_decision(self, actions, source_id, interaction_type, event_type, *, record):
        grouped_actions = self.judge_group(actions, source_id, interaction_type, event_type)
        group_results = [
            TrackGroupResult(
                action_ids=[action.action_id for action in group],
                reason="fake grouping",
            )
            for group in grouped_actions
        ]
        return TrackJudgeDecision(
            groups=grouped_actions,
            group_results=group_results,
            source="llm",
            model="fake-model",
        )

    def record_judgment(self, group_key, actions, groups, *, source, model):
        self.judgments.append(
            {
                "group_key": group_key,
                "input_action_ids": [action.action_id for action in actions],
                "output_groups": groups,
                "source": source,
                "model": model,
            }
        )


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
            ("sword", "sfx", "onset"): [["known", "reassign"]],
        }
    )
    result = synthesize_tracks(
        [
            make_action("known", "sword", "sfx", "short metal tap", {"type": "onset", "timestamp": 0.1}),
            make_action(
                "reassign",
                "UNKNOWN_OBJECT_CUT001_1",
                "sfx",
                "longer metal tap",
                {"type": "onset", "timestamp": 0.3},
                unknown=True,
                suggestion="REASSIGN_TO_EXISTING",
                suggested_entity_id="sword",
            ),
        ],
        track_judge=judge,
    )

    assert len(result.track_manifest.tracks) == 1
    assert result.track_manifest.tracks[0].source_entity_id == "sword"
    assert len(result.track_manifest.tracks[0].events) == 2
    assert result.track_manifest.tracks[0].track_number == 1


def test_synthesize_tracks_marks_ambience_without_llm_grouping():
    judge = FakeTrackJudge()
    result = synthesize_tracks(
        [
            make_action(
                "amb1",
                "wind",
                "ambience",
                "room tone",
                {"type": "continuous", "start_time": 0.0, "end_time": 2.0},
            ),
            make_action(
                "amb2",
                "wind",
                "ambience",
                "steady room tone",
                {"type": "continuous", "start_time": 2.0, "end_time": 4.0},
            ),
        ],
        track_judge=judge,
    )

    assert judge.calls == []
    assert len(result.track_manifest.tracks) == 1
    assert result.track_manifest.tracks[0].track_id == "wind__ambience"
    assert result.track_manifest.tracks[0].track_type == "ambience"
    assert result.track_manifest.tracks[0].track_number == 1


def test_synthesize_tracks_splits_onset_and_continuous_before_track_judge():
    judge = FakeTrackJudge()
    result = synthesize_tracks(
        [
            make_action("a1", "sword", "sfx", "tap", {"type": "onset", "timestamp": 0.1}),
            make_action(
                "a2",
                "sword",
                "sfx",
                "rolling noise",
                {"type": "continuous", "start_time": 0.2, "end_time": 0.8},
            ),
        ],
        track_judge=judge,
    )

    assert judge.calls == []
    assert {track.track_id for track in result.track_manifest.tracks} == {
        "sword__sfx__onset",
        "sword__sfx__continuous",
    }


def test_synthesize_tracks_uses_desc_key_for_multi_group_sfx_ids():
    judge = FakeTrackJudge(
        {
            ("fighter_footstep", "sfx", "onset"): [["step_a"], ["step_b"]],
        }
    )
    result = synthesize_tracks(
        [
            make_action("step_a", "fighter_footstep", "sfx", "Soft sneaker step on tile", {"type": "onset", "timestamp": 0.1}),
            make_action("step_b", "fighter_footstep", "sfx", "Heavy boot step on gravel", {"type": "onset", "timestamp": 0.4}),
        ],
        track_judge=judge,
    )

    track_ids = {track.track_id for track in result.track_manifest.tracks}
    assert "fighter_footstep__sfx__onset__soft_sneaker_step_on_tile" in track_ids
    assert "fighter_footstep__sfx__onset__heavy_boot_step_on_gravel" in track_ids


def test_synthesize_tracks_adds_hash_suffix_when_desc_keys_collide():
    judge = FakeTrackJudge(
        {
            ("fighter_footstep", "sfx", "onset"): [["step_a"], ["step_b"]],
        }
    )
    result = synthesize_tracks(
        [
            make_action(
                "step_a",
                "fighter_footstep",
                "sfx",
                "Very long repeated description for collision alpha variation",
                {"type": "onset", "timestamp": 0.1},
            ),
            make_action(
                "step_b",
                "fighter_footstep",
                "sfx",
                "Very long repeated description for collision beta variation",
                {"type": "onset", "timestamp": 0.4},
            ),
        ],
        track_judge=judge,
    )

    track_ids = [track.track_id for track in result.track_manifest.tracks]
    assert len(track_ids) == 2
    assert track_ids[0] != track_ids[1]
    assert all(track_id.startswith("fighter_footstep__sfx__onset__very_long_repeated_description_for_coll") for track_id in track_ids)


def test_synthesize_tracks_sorts_events_within_a_group():
    judge = FakeTrackJudge(
        {
            ("sword", "sfx", "onset"): [["late", "early"]],
        }
    )
    result = synthesize_tracks(
        [
            make_action("late", "sword", "sfx", "late hit", {"type": "onset", "timestamp": 0.7}),
            make_action("early", "sword", "sfx", "early hit", {"type": "onset", "timestamp": 0.2}),
        ],
        track_judge=judge,
    )

    track = result.track_manifest.tracks[0]
    assert [event.timestamp for event in track.events] == [0.2, 0.7]
    assert track.sound_description == "early hit"


def test_synthesize_tracks_deterministically_merges_same_normalized_description_without_track_judge():
    result = synthesize_tracks(
        [
            make_action("a1", "sword", "sfx", "Footstep!!!", {"type": "onset", "timestamp": 0.1}),
            make_action("a2", "sword", "sfx", "Footstep???", {"type": "onset", "timestamp": 0.2}),
        ]
    )

    assert len(result.track_manifest.tracks) == 1
    assert result.track_manifest.tracks[0].track_id == "sword__sfx__onset"
    assert len(result.track_manifest.tracks[0].events) == 2


def test_synthesize_tracks_records_deterministic_judgment_for_same_normalized_description():
    judge = TrackJudge(flash_client=None)
    result = synthesize_tracks(
        [
            make_action("a1", "fighter_footstep", "sfx", "Strained grunt!!!", {"type": "onset", "timestamp": 0.1}),
            make_action("a2", "fighter_footstep", "sfx", "Strained grunt???", {"type": "onset", "timestamp": 0.3}),
        ],
        track_judge=judge,
    )

    assert len(result.track_manifest.tracks) == 1
    judgments = judge.get_judgments()
    assert len(judgments) == 1
    assert judgments[0].source == "deterministic"
    assert judgments[0].group_key == "fighter_footstep__sfx__onset"


def test_synthesize_tracks_uses_deterministic_groups_before_llm_grouping():
    class FakeDecisionJudge(TrackJudge):
        def __init__(self):
            super().__init__(flash_client=None)
            self.decision_calls: list[list[str]] = []

        def judge_group_decision(self, actions, source_id, interaction_type, event_type, *, record):
            self.decision_calls.append([action.action_id for action in actions])
            rep_groups = [
                TrackGroupResult(action_ids=[actions[0].action_id, actions[1].action_id], reason="same family"),
            ]
            return TrackJudgeDecision(
                groups=[list(actions)],
                group_results=rep_groups,
                source="llm",
                model=self.model,
            )

    judge = FakeDecisionJudge()
    result = synthesize_tracks(
        [
            make_action("a1", "sword", "sfx", "Metal clash!!!", {"type": "onset", "timestamp": 0.1}),
            make_action("a2", "sword", "sfx", "Metal clash???", {"type": "onset", "timestamp": 0.2}),
            make_action("a3", "sword", "sfx", "Heavy wooden thud", {"type": "onset", "timestamp": 0.3}),
        ],
        track_judge=judge,
    )

    assert judge.decision_calls == [["a1", "a3"]]
    assert len(result.track_manifest.tracks) == 1
    assert len(result.track_manifest.tracks[0].events) == 3


def test_synthesize_tracks_sorts_manifest_and_assigns_track_numbers():
    result = synthesize_tracks(
        [
            make_action(
                "amb",
                "wind",
                "ambience",
                "steady room tone",
                {"type": "continuous", "start_time": 0.0, "end_time": 2.0},
            ),
            make_action(
                "sfx",
                "sword",
                "sfx",
                "wooden knock",
                {"type": "onset", "timestamp": 0.2},
            ),
        ]
    )

    assert [track.track_type for track in result.track_manifest.tracks] == ["sfx", "ambience"]
    assert [track.track_number for track in result.track_manifest.tracks] == [1, 2]
    assert [track.track_id for track in result.track_manifest.tracks] == [
        "sword__sfx__onset",
        "wind__ambience",
    ]


def test_synthesize_tracks_drops_legacy_voice_actions_with_warning():
    voice_action = Action.model_construct(
        action_id="voice",
        cut_id="CUT_001",
        primary_source_id="fighter",
        unknown_resolution=None,
        interaction_type="voice",
        sound_description="legacy voice sound",
        observed_visual_description="fighter shouting",
        event=OnsetEvent(type="onset", timestamp=0.5),
        boundary_flag=False,
    )
    result = synthesize_tracks([voice_action])
    assert result.track_manifest.tracks == []
    assert [warning.code for warning in result.warnings] == ["STAGE06_LEGACY_VOICE_DROPPED"]
