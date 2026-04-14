import unittest

from v2t_prototype import PipelineResult, UnknownResolution
from v2t_prototype.synthesizer import synthesize_tracks
from v2t_prototype.surface_judge import SurfaceJudgmentResult


def make_action(
    action_id: str,
    primary_source_id: str,
    interaction_type: str,
    sound_description: str,
    surface_context: str | None,
    timestamp: float,
    unknown: bool = False,
    suggestion: str | None = None,
    suggested_entity_id: str | None = None,
) -> "Action":
    from v2t_prototype import Action

    resolution = None
    if unknown:
        resolution = UnknownResolution(
            suggestion=suggestion or "UNRESOLVED",
            reason="test",
            suggested_entity_id=suggested_entity_id,
        )

    return Action(
        action_id=action_id,
        cut_id="cut_001",
        primary_source_id=primary_source_id,
        unknown_resolution=resolution,
        interaction_type=interaction_type,
        sound_description=sound_description,
        surface_context=surface_context,
        observed_visual_description="desc",
        event={"type": "onset", "timestamp": timestamp},
        boundary_flag=False,
    )


class SynthesizerTest(unittest.TestCase):

    def test_synthesize_tracks_populates_manifest_and_unresolved(self):
        actions = [
            make_action("act_1", "char_001", "foley", "short step", "tile floor", 0.2),
            make_action(
                "act_2",
                "char_001",
                "foley",
                "longer tiled footsteps",
                "tile floor",
                0.4,
            ),
            make_action("act_3", "obj_001", "hard_effect", "gentle tap", "wood counter", 0.7),
            make_action(
                "unknown_act",
                "UNKNOWN_TOOL",
                "hard_effect",
                "metal tap",
                "metal",
                1.0,
                unknown=True,
            ),
        ]

        result: PipelineResult = synthesize_tracks(actions)

        tracks = result.track_manifest.tracks
        self.assertEqual(len(tracks), 2)
        self.assertEqual(len(result.unresolved_unknowns), 1)

        footstep_track = next(track for track in tracks if track.source_entity_id == "char_001")
        self.assertEqual(footstep_track.sound_description, "longer tiled footsteps")
        self.assertEqual(footstep_track.surface_context_summary, "tile floor")
        self.assertEqual([event.timestamp for event in footstep_track.events], [0.2, 0.4])

    def test_unresolved_unknowns_translate_to_pipeline_result(self):
        unknown = make_action(
            "unknown_act",
            "UNKNOWN_DEVICE",
            "electronic",
            "beep",
            None,
            0.0,
            unknown=True,
        )
        result: PipelineResult = synthesize_tracks([unknown])

        self.assertEqual(result.track_manifest.tracks, [])
        self.assertEqual(len(result.unresolved_unknowns), 1)
        self.assertEqual(result.unresolved_unknowns[0].unknown_id, "UNKNOWN_DEVICE")

    def test_reassign_unknown_uses_suggested_entity(self):
        actions = [
            make_action(
                "known",
                "obj_001",
                "hard_effect",
                "metal tap",
                "metal surface",
                0.1,
            ),
            make_action(
                "reassign",
                "UNKNOWN_TOOL",
                "hard_effect",
                "metal tap again",
                "metal surface",
                0.3,
                unknown=True,
                suggestion="REASSIGN_TO_EXISTING",
                suggested_entity_id="obj_001",
            ),
        ]

        result = synthesize_tracks(actions)
        tracks = result.track_manifest.tracks
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].source_entity_id, "obj_001")
        self.assertEqual(len(tracks[0].events), 2)

    def test_track_type_depending_on_source_kind(self):
        actions = [
            make_action("amb1", "amb_001", "background", "room tone", None, 0.0),
            make_action("sfx1", "char_001", "foley", "footsteps", "tile floor", 0.5),
        ]

        result = synthesize_tracks(
            actions, source_entity_kind_by_id={"amb_001": "AmbienceSource"}
        )
        types = {track.source_entity_id: track.track_type for track in result.track_manifest.tracks}
        self.assertEqual(types["amb_001"], "ambience")
        self.assertEqual(types["char_001"], "sfx")

    def test_track_id_follows_deterministic_surface_key_rule(self):
        # deterministic track_id 규칙이 surface_key 기반으로 잘 작동하는지 확인합니다.
        actions = [
            make_action(
                "foot1",
                "char_001",
                "foley",
                "wet step",
                "Wet Cobblestone, Slightly Wet",
                0.1,
            ),
            make_action(
                "foot2",
                "char_001",
                "foley",
                "wet step repeat",
                "Wet Cobblestone, Slightly Wet",
                0.3,
            ),
            make_action(
                "foot3",
                "char_001",
                "foley",
                "grass step",
                "Grass Path",
                0.5,
            ),
            make_action(
                "hard1",
                "obj_001",
                "hard_effect",
                "rail hit",
                "Blue Metal Railing - Painted",
                0.6,
            ),
            make_action(
                "hard2",
                "obj_001",
                "hard_effect",
                "rail hit again",
                "Blue Metal Railing - Painted",
                0.8,
            ),
            make_action(
                "hard_unknown",
                "obj_002",
                "hard_effect",
                "clack",
                None,
                1.0,
            ),
            make_action(
                "amb",
                "amb_001",
                "background",
                "room tone",
                None,
                1.2,
            ),
            make_action(
                "elec",
                "obj_003",
                "electronic",
                "drone",
                None,
                1.4,
            ),
        ]

        result = synthesize_tracks(
            actions,
            source_entity_kind_by_id={"amb_001": "AmbienceSource"},
        )
        track_ids = {track.track_id: track for track in result.track_manifest.tracks}

        self.assertIn("char_001__foley__wet_cobblestone_slightly_wet", track_ids)
        self.assertIn("char_001__foley__grass_path", track_ids)
        self.assertIn("obj_001__hard_effect__blue_metal_railing_painted", track_ids)
        self.assertTrue(
            any(
                track_id.startswith("obj_002__hard_effect__missing_surface_")
                for track_id in track_ids
            )
        )
        self.assertIn("amb_001__background", track_ids)
        self.assertIn("obj_003__electronic", track_ids)
        self.assertEqual(track_ids["amb_001__background"].track_type, "ambience")

    def test_missing_surface_groups_get_unique_track_ids(self):
        # surface_context_summary가 없는 다수 그룹에서 hash 기반 ID가 서로 충돌하지 않는지 확인합니다.
        actions = [
            make_action("split_a", "char_001", "foley", "foot", None, 0.1),
            make_action("split_b", "char_001", "foley", "foot", None, 0.3),
        ]

        result = synthesize_tracks(actions)
        missing_tracks = [
            track
            for track in result.track_manifest.tracks
            if track.surface_context_summary is None and track.interaction_type == "foley"
        ]

        self.assertEqual(2, len(missing_tracks))
        self.assertNotEqual(missing_tracks[0].track_id, missing_tracks[1].track_id)
        self.assertTrue(
            all(
                track.track_id.startswith("char_001__foley__missing_surface_")
                for track in missing_tracks
            )
        )

    def test_surface_judge_groups_variants_by_representative_pairs(self):
        class FakeSurfaceJudge:
            def __init__(self):
                self.calls = []

            def judge(self, action_a, action_b, interaction_type):
                self.calls.append((action_a.surface_context, action_b.surface_context, interaction_type))
                surfaces = {action_a.surface_context, action_b.surface_context}
                if surfaces == {"glass table", "glass-topped table"}:
                    return SurfaceJudgmentResult(
                        result="COMPATIBLE",
                        reason="Equivalent glass table surfaces.",
                        source="flash",
                        representative_surface="glass-topped table",
                    )
                return SurfaceJudgmentResult(
                    result="INCOMPATIBLE",
                    reason="Different surface family.",
                    source="flash",
                    representative_surface=None,
                )

        actions = [
            make_action("a1", "obj_001", "hard_effect", "tap", "glass table", 0.1),
            make_action("a2", "obj_001", "hard_effect", "tap", "glass-topped table", 0.2),
            make_action("a3", "obj_001", "hard_effect", "tap", "rubber mat", 0.3),
            make_action("a4", "obj_001", "hard_effect", "tap", "glass table", 0.4),
        ]

        judge = FakeSurfaceJudge()
        result = synthesize_tracks(actions, surface_judge=judge)

        self.assertEqual(2, len(result.track_manifest.tracks))
        self.assertEqual(3, len(judge.calls))

    def test_background_and_electronic_skip_surface_judge(self):
        class FakeSurfaceJudge:
            def __init__(self):
                self.calls = []

            def judge(self, action_a, action_b, interaction_type):
                self.calls.append((action_a.action_id, action_b.action_id, interaction_type))
                return SurfaceJudgmentResult(
                    result="INCOMPATIBLE",
                    reason="should not be used",
                    source="flash",
                    representative_surface=None,
                )

        background_actions = [
            make_action("bg1", "amb_001", "background", "tone", None, 0.0),
            make_action("bg2", "amb_001", "background", "tone", "different", 0.3),
        ]
        electronic_actions = [
            make_action("el1", "obj_001", "electronic", "hum", None, 0.4),
            make_action("el2", "obj_001", "electronic", "hum", "panel", 0.8),
        ]

        judge = FakeSurfaceJudge()
        result = synthesize_tracks(background_actions + electronic_actions, surface_judge=judge)

        self.assertEqual(2, len(result.track_manifest.tracks))
        self.assertEqual([], judge.calls)

    def test_all_null_surface_group_emits_info_warning_when_surface_judge_enabled(self):
        class FakeSurfaceJudge:
            def judge(self, action_a, action_b, interaction_type):
                raise AssertionError("single null variant should not trigger judge")

        actions = [
            make_action("null1", "char_001", "foley", "step", None, 0.1),
            make_action("null2", "char_001", "foley", "step repeat", None, 0.2),
        ]

        result = synthesize_tracks(actions, surface_judge=FakeSurfaceJudge())

        self.assertEqual(1, len(result.track_manifest.tracks))
        self.assertEqual(["SURFACE_ALL_NULL"], [item.code for item in result.warnings])
