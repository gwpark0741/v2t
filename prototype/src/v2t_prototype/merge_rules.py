from __future__ import annotations

from typing import Callable, Optional

from .models import Action


SurfaceCompatibility = Callable[[str, str], bool]


def default_surface_compatibility(surface_a: str, surface_b: str) -> bool:
    """기본 surface 비교기: 문자열을 소문자로 정규화한 후 단순 일치 여부를 확인합니다."""
    normalized_a = surface_a.strip().lower()
    normalized_b = surface_b.strip().lower()
    return normalized_a == normalized_b


def canonical_should_merge(
    action_a: Action,
    action_b: Action,
    surface_compatibility: Optional[SurfaceCompatibility] = None,
) -> bool:
    """두 액션의 동일 트랙 여부를 판정합니다."""
    if action_a.primary_source_id != action_b.primary_source_id:
        return False
    if action_a.interaction_type != action_b.interaction_type:
        return False

    compatibility = surface_compatibility or default_surface_compatibility
    surface_a = action_a.surface_context
    surface_b = action_b.surface_context

    interaction = action_a.interaction_type
    if interaction in {"hard_effect", "foley"}:
        # hard_effect/foley는 surface 호환이 필수입니다.
        if not surface_a or not surface_b:
            return False
        return compatibility(surface_a, surface_b)

    return True
