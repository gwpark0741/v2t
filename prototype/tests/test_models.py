import pytest

from v2t_prototype.models import (
    Action,
    ContinuousEvent,
    OnsetEvent,
    Track,
    TrackManifest,
    UnknownResolution,
)


def test_action_requires_unknown_resolution_for_unknown_source():
    OnsetEvent(type="onset", timestamp=0.0)
    action = Action(
        action_id="act_001",
        cut_id="CUT_001",
        primary_source_id="UNKNOWN_OBJECT_CUT001_1",
        unknown_resolution=UnknownResolution(
            suggestion="UNRESOLVED",
            suggested_entity_id=None,
            reason="low confidence",
        ),
        interaction_type="sfx",
        sound_description="mysterious clang",
        observed_visual_description="unidentified metal bar",
        event=OnsetEvent(type="onset", timestamp=0.2),
        boundary_flag=False,
    )
    assert action.unknown_resolution is not None


def test_action_missing_unknown_resolution_rejected():
    with pytest.raises(ValueError):
        Action(
            action_id="act_002",
            cut_id="CUT_001",
            primary_source_id="UNKNOWN_OBJECT_CUT001_2",
            interaction_type="sfx",
            sound_description="footstep",
            observed_visual_description="unknown foot",
            event=OnsetEvent(type="onset", timestamp=0.4),
            boundary_flag=False,
        )


def test_track_requires_at_least_one_event():
    with pytest.raises(ValueError):
        Track(
            track_id="trk_001",
            track_type="sfx",
            source_entity_id="char_001",
            interaction_type="sfx",
            sound_description="footsteps on tile",
            events=[],
        )


def test_track_manifest_accepts_tracks():
    track = Track(
        track_id="trk_002",
        track_type="sfx",
        source_entity_id="obj_001",
        interaction_type="sfx",
        sound_description="cup contact",
        events=[OnsetEvent(type="onset", timestamp=0.2)],
    )
    manifest = TrackManifest(tracks=[track])
    assert manifest.tracks[0].track_id == "trk_002"
