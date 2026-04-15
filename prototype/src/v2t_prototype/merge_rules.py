from __future__ import annotations

from .models import Action


def canonical_should_merge(
    action_a: Action,
    action_b: Action,
) -> bool | None:
    """Ambience 계열만 canonical merge를 확정하고, sfx/voice는 Stage 06에서 처리합니다."""
    if action_a.primary_source_id != action_b.primary_source_id:
        return False
    if action_a.interaction_type != action_b.interaction_type:
        return False
    if action_a.interaction_type == "ambience":
        return True
    return None
