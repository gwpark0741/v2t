from __future__ import annotations

from .models import Action


def canonical_should_merge(
    action_a: Action,
    action_b: Action,
) -> bool | None:
    """Ambience 계열의 동일 트랙 여부를 판정합니다."""
    if action_a.primary_source_id != action_b.primary_source_id:
        return False
    if action_a.interaction_type != action_b.interaction_type:
        return False
    if action_a.interaction_type == "ambience":
        return True
    return None
