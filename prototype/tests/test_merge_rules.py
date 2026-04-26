import unittest

from v2t_prototype import Action
from v2t_prototype.merge_rules import canonical_should_merge


def make_action(
    primary_source_id: str,
    interaction_type: str,
) -> Action:
    return Action(
        action_id=f"{primary_source_id}-{interaction_type}",
        cut_id="cut_001",
        primary_source_id=primary_source_id,
        unknown_resolution=None,
        interaction_type=interaction_type,
        sound_description="sound",
        observed_visual_description="desc",
        event={"type": "onset", "timestamp": 0.0},
        boundary_flag=False,
    )


class MergeRulesTest(unittest.TestCase):
    def test_ambience_merges_when_source_and_interaction_match(self):
        base = make_action("wind", "ambience")
        other = make_action("wind", "ambience")
        self.assertTrue(canonical_should_merge(base, other))

    def test_sfx_never_merges_via_canonical_rule(self):
        base = make_action("sword", "sfx")
        other = make_action("sword", "sfx")
        self.assertIsNone(canonical_should_merge(base, other))

    def test_mismatched_sources_do_not_merge(self):
        base = make_action("wind", "ambience")
        other = make_action("rain", "ambience")
        self.assertFalse(canonical_should_merge(base, other))
