import pytest

from v2t_prototype.entity_registry import (
    derive_interaction_type,
    get_all_valid_targets,
    get_ambience_targets,
    get_cut_hints,
    get_sfx_targets,
)
from v2t_prototype.models import (
    Action,
    AgentAResponse,
    Ambience,
    CutMapping,
    CutSourceMapping,
    Entity,
    Entity_Child,
    EntityRegistry,
    OnsetEvent,
    Track,
    TrackManifest,
    Unknown,
    UnknownResolution,
)


def test_entity_registry_accepts_new_hierarchy_contract():
    registry = EntityRegistry(
        entities=[
            Entity(
                id="baby",
                label="baby",
                children=[
                    Entity_Child(id="baby_footstep", label="baby footstep"),
                ],
            )
        ],
        ambience=[Ambience(id="wind", label="wind")],
        unknowns=[
            Unknown(
                id="unknown_1",
                label="unknown 1",
                visual_description="wrapped cylindrical object on a character's back",
            )
        ],
    )

    assert registry.entities[0].children[0].id == "baby_footstep"
    assert registry.unknowns[0].id == "unknown_1"


def test_childless_entity_omits_children_when_serialized():
    payload = Entity(id="tree", label="tree").model_dump(mode="json")

    assert payload == {"id": "tree", "label": "tree"}


def test_entity_with_children_serializes_children():
    payload = Entity(
        id="fighter",
        label="fighter",
        children=[Entity_Child(id="fighter_sword", label="fighter sword")],
    ).model_dump(mode="json")

    assert payload == {
        "id": "fighter",
        "label": "fighter",
        "children": [{"id": "fighter_sword", "label": "fighter sword"}],
    }


def test_ambience_rejects_children_field():
    with pytest.raises(ValueError):
        Ambience.model_validate(
            {
                "id": "night_arena",
                "label": "night arena",
                "children": [{"id": "banner_flag", "label": "banner flag"}],
            }
        )


def test_registry_target_helpers_return_expected_nodes():
    registry = EntityRegistry(
        entities=[
            Entity(
                id="fighter",
                label="fighter",
                children=[Entity_Child(id="fighter_sword", label="fighter sword")],
            ),
            Entity(id="tree", label="tree"),
        ],
        ambience=[Ambience(id="night_arena", label="night arena")],
        unknowns=[Unknown(id="unknown_1", label="unknown 1", visual_description="hidden object")],
    )

    assert sorted(get_sfx_targets(registry)) == ["fighter_sword", "tree"]
    assert sorted(get_ambience_targets(registry)) == ["night_arena"]
    assert sorted(get_all_valid_targets(registry)) == ["fighter_sword", "night_arena", "tree"]


def test_get_cut_hints_returns_matching_mapping_or_none():
    cut_mapping = CutMapping(
        mappings=[
            CutSourceMapping(
                cut_id="CUT_001",
                sfx_source_ids=["fighter_sword"],
                ambience_source_ids=["night_arena"],
            )
        ]
    )

    assert get_cut_hints(cut_mapping, "CUT_001") == cut_mapping.mappings[0]
    assert get_cut_hints(cut_mapping, "CUT_999") is None
    assert get_cut_hints(CutMapping(), "CUT_001") is None


def test_derive_interaction_type_matches_target_class():
    registry = EntityRegistry(
        entities=[
            Entity(id="tree", label="tree"),
            Entity(
                id="fighter",
                label="fighter",
                children=[Entity_Child(id="fighter_sword", label="fighter sword")],
            ),
        ],
        ambience=[Ambience(id="night_arena", label="night arena")],
    )

    assert derive_interaction_type("tree", registry) == "sfx"
    assert derive_interaction_type("fighter_sword", registry) == "sfx"
    assert derive_interaction_type("night_arena", registry) == "ambience"
    assert derive_interaction_type("UNKNOWN_OBJECT_CUT001_1", registry) is None
    assert derive_interaction_type("missing_source", registry) is None


def test_action_requires_unknown_resolution_for_unknown_source():
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
    )
    assert action.unknown_resolution is not None


def test_agent_a_response_cut_mapping_roundtrip_and_defaults():
    response = AgentAResponse(
        entity_registry=EntityRegistry(
            entities=[Entity(id="tree", label="tree")],
            ambience=[Ambience(id="wind", label="wind")],
            unknowns=[],
        ),
        cut_mapping=CutMapping(
            mappings=[
                CutSourceMapping(
                    cut_id="CUT_001",
                    sfx_source_ids=["tree"],
                    ambience_source_ids=["wind"],
                )
            ]
        ),
    )

    payload = response.model_dump(mode="json")
    assert payload["cut_mapping"]["mappings"][0]["cut_id"] == "CUT_001"
    roundtrip = AgentAResponse.model_validate(payload)
    assert roundtrip.cut_mapping.mappings[0].sfx_source_ids == ["tree"]

    defaulted = AgentAResponse.model_validate({"entity_registry": {"entities": [], "ambience": [], "unknowns": []}})
    assert defaulted.cut_mapping.mappings == []


def test_agent_a_response_cut_mapping_default_is_not_shared():
    first = AgentAResponse.model_validate(
        {"entity_registry": {"entities": [], "ambience": [], "unknowns": []}}
    )
    second = AgentAResponse.model_validate(
        {"entity_registry": {"entities": [], "ambience": [], "unknowns": []}}
    )

    first.cut_mapping.mappings.append(CutSourceMapping(cut_id="CUT_001"))

    assert second.cut_mapping.mappings == []


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
        )


def test_track_requires_at_least_one_event():
    with pytest.raises(ValueError):
        Track(
            track_number=1,
            track_id="trk_001",
            track_type="sfx",
            source_entity_id="baby_footstep",
            sound_description="footsteps on tile",
            events=[],
        )


def test_track_manifest_accepts_sfx_and_ambience_tracks():
    sfx_track = Track(
        track_number=1,
        track_id="trk_sfx",
        track_type="sfx",
        source_entity_id="baby_footstep",
        sound_description="cup contact",
        events=[OnsetEvent(type="onset", timestamp=0.2)],
    )
    ambience_track = Track(
        track_number=2,
        track_id="trk_amb",
        track_type="ambience",
        source_entity_id="wind",
        sound_description="steady wind",
        events=[OnsetEvent(type="onset", timestamp=0.4)],
    )
    manifest = TrackManifest(tracks=[sfx_track, ambience_track])
    assert [track.track_type for track in manifest.tracks] == ["sfx", "ambience"]


def test_track_rejects_non_positive_track_number():
    with pytest.raises(ValueError):
        Track(
            track_number=0,
            track_id="trk_003",
            track_type="sfx",
            source_entity_id="baby_footstep",
            sound_description="cup contact",
            events=[OnsetEvent(type="onset", timestamp=0.2)],
        )


def test_action_rejects_voice_interaction_type():
    with pytest.raises(ValueError):
        Action(
            action_id="act_voice_001",
            cut_id="CUT_001",
            primary_source_id="baby",
            interaction_type="voice",
            sound_description="sharp vocal grunt of effort",
            observed_visual_description="fighter exhales while swinging",
            event=OnsetEvent(type="onset", timestamp=0.6),
        )
