import unittest

from v2t_prototype import Action
from v2t_prototype.merge_rules import canonical_should_merge


def make_action(
    primary_source_id: str,
    interaction_type: str,
    surface_context: str | None,
) -> Action:
    return Action(
        action_id=f"{primary_source_id}-{interaction_type}",
        cut_id="cut_001",
        primary_source_id=primary_source_id,
        unknown_resolution=None,
        interaction_type=interaction_type,
        sound_description="sound",
        surface_context=surface_context,
        observed_visual_description="desc",
        event={"type": "onset", "timestamp": 0.0},
        boundary_flag=False,
    )


class MergeRulesTest(unittest.TestCase):
    # hard_effect/foley는 surface 호환을 반드시 확인하고 background는 surface 무시 규칙을 테스트합니다.

    def test_hard_effect_merges_only_on_matching_surfaces(self):
        base = make_action("obj_001", "hard_effect", "wood counter")
        matching = make_action("obj_001", "hard_effect", "wood counter")
        self.assertTrue(canonical_should_merge(base, matching))

        mismatched = make_action("obj_001", "hard_effect", "metal panel")
        self.assertFalse(canonical_should_merge(base, mismatched))

        missing_surface = make_action("obj_001", "hard_effect", None)
        self.assertFalse(canonical_should_merge(base, missing_surface))

    def test_foley_requires_compat_when_surfaces_available(self):
        base = make_action("char_001", "foley", "tile floor")
        matching = make_action("char_001", "foley", "tile floor")
        self.assertTrue(canonical_should_merge(base, matching))

        mismatch = make_action("char_001", "foley", "wood floor")
        self.assertFalse(canonical_should_merge(base, mismatch))

        missing_surface = make_action("char_001", "foley", None)
        self.assertFalse(canonical_should_merge(base, missing_surface))

    def test_background_ignores_surfaces(self):
        base = make_action("amb_001", "background", "indoor tone")
        other = make_action("amb_001", "background", "outdoor tone")
        self.assertTrue(canonical_should_merge(base, other))

    def test_custom_surface_function_applied(self):
        base = make_action("obj_001", "hard_effect", "A")
        other = make_action("obj_001", "hard_effect", "B")

        def always_compatible(_: str, __: str) -> bool:
            return True

        self.assertTrue(
            canonical_should_merge(base, other, surface_compatibility=always_compatible)
        )
