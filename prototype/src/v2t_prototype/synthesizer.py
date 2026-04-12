from __future__ import annotations

import hashlib
import re

from typing import Callable, Dict, List, Optional

from .merge_rules import canonical_should_merge
from .models import (
    Action,
    PipelineResult,
    Track,
    TrackManifest,
    TrackType,
    UnresolvedUnknown,
)


def _is_unresolved_unknown(action: Action) -> bool:
    """UNKNOWN_*이면서 UNRESOLVED로 표시된 경우를 검출하여 unresolved_unknowns로 분리합니다."""
    return (
        action.primary_source_id.startswith("UNKNOWN_")
        and action.unknown_resolution is not None
        and action.unknown_resolution.suggestion == "UNRESOLVED"
    )


def _normalize_source_id(action: Action) -> str:
    """
    REASSIGN_TO_EXISTING인 경우에 suggested_entity_id로 교체하여 추후 merge 때 실제 엔티티를 참조하게 합니다.
    """
    if (
        action.primary_source_id.startswith("UNKNOWN_")
        and action.unknown_resolution is not None
        and action.unknown_resolution.suggestion == "REASSIGN_TO_EXISTING"
        and action.unknown_resolution.suggested_entity_id
    ):
        return action.unknown_resolution.suggested_entity_id
    return action.primary_source_id


def _resolved_action(action: Action) -> Action:
    """
    Action을 mutate하지 않고, 필요한 경우 normalized_id로 교체한 복사본을 반환합니다.
    """
    normalized_id = _normalize_source_id(action)
    if normalized_id == action.primary_source_id:
        return action
    return action.model_copy(update={"primary_source_id": normalized_id})


def _track_type_for(
    source_id: str, source_entity_kind_by_id: Optional[Dict[str, str]]
) -> TrackType:
    """source_entity_kind_by_id 맵을 참조하여 ambience/sfx를 결정합니다."""
    if source_entity_kind_by_id and source_entity_kind_by_id.get(source_id) == "AmbienceSource":
        return "ambience"
    return "sfx"


def _longest_non_null(strings: List[str]) -> Optional[str]:
    """가장 긴 문자열을 찾아서 대표 surface_summary로 사용합니다."""
    if not strings:
        return None
    return max(strings, key=len)


def normalize_surface_key(surface: str) -> str:
    """surface_context_summary를 트랙 ID에 안전하게 사용할 수 있도록 정규화합니다."""
    normalized = surface.lower().strip()
    normalized = re.sub(r"[^a-z0-9\s]", "", normalized)
    normalized = re.sub(r"\s+", "_", normalized)
    return normalized[:50] or "unknown_surface"


def _missing_surface_key_for_group(group: List[Action]) -> str:
    """
    surface_context_summary가 없을 때, group 내 고정 정보(action_id, 시간 등)를 이용해 결정론적인 키를 생성합니다.
    이 키는 해시를 사용하여 길이를 제한하되, 동일한 group이면 항상 같은 결과가 나오게 설계합니다.
    """
    sorted_ids = sorted(entry.action_id for entry in group)
    event_signatures = []
    for entry in sorted(group, key=lambda entry: entry.action_id):
        ev = entry.event
        if hasattr(ev, "timestamp"):
            event_signatures.append(f"{ev.timestamp:.2f}")
        else:
            event_signatures.append(f"{ev.start_time:.2f}-{ev.end_time:.2f}")
    material = "_".join(["-".join(sorted_ids), "-".join(event_signatures)])
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:8]
    return f"missing_surface_{digest}"


def _surface_key_for_group(group: List[Action], surface_summary: Optional[str]) -> str:
    """
    surface_summary가 존재하면 정규화된 surface_key를 사용하고, 없으면 group 기반 missing_surface 키를 생성합니다.
    """
    if surface_summary:
        return normalize_surface_key(surface_summary)
    return _missing_surface_key_for_group(group)


def _build_track_id(
    source_id: str, interaction_type: str, surface_key: str
) -> str:
    """
    결정론적인 track_id를 생성합니다.
    background/electronic은 surface_key를 제외하고, hard_effect/foley는 surface_key를 접미사로 사용합니다.
    """
    if interaction_type in {"background", "electronic"}:
        return f"{source_id}__{interaction_type}"
    return f"{source_id}__{interaction_type}__{surface_key}"


def synthesize_tracks(
    actions: List[Action],
    surface_compatibility: Optional[Callable[[str, str], bool]] = None,
    source_entity_kind_by_id: Optional[Dict[str, str]] = None,
) -> PipelineResult:
    groups: List[List[Action]] = []
    unresolved_unknowns: List[UnresolvedUnknown] = []

    for action in actions:
        if _is_unresolved_unknown(action):
            unresolved_unknowns.append(
                UnresolvedUnknown(
                    unknown_id=action.primary_source_id,
                    cut_id=action.cut_id,
                    observed_visual_description=action.observed_visual_description,
                    interaction_type=action.interaction_type,
                    sound_description=action.sound_description,
                )
            )
            continue

        resolved = _resolved_action(action)
        matched = False
        for group in groups:
            representative = group[0]
            if canonical_should_merge(representative, resolved, surface_compatibility):
                group.append(resolved)
                matched = True
                break

        if not matched:
            groups.append([resolved])

    tracks: List[Track] = []
    for group in groups:
        # canonical_should_merge() 기준으로 group이 정해진 이후,
        # 대표 sound_description과 surface_summary(있는 경우)를 정리합니다.
        sound_choice = max(group, key=lambda entry: len(entry.sound_description))
        surfaces = [entry.surface_context for entry in group if entry.surface_context]
        source_id = group[0].primary_source_id
        track_type = _track_type_for(source_id, source_entity_kind_by_id)
        surface_summary = _longest_non_null(surfaces)
        surface_key = _surface_key_for_group(group, surface_summary)
        track_id = _build_track_id(source_id, group[0].interaction_type, surface_key)

        # 결정론적 track_id를 규칙대로 계산하고 surface_summary는 트랙 메타데이터로 유지합니다.
        tracks.append(
            Track(
                track_id=track_id,
                track_type=track_type,
                source_entity_id=source_id,
                interaction_type=group[0].interaction_type,
                sound_description=sound_choice.sound_description,
                surface_context_summary=surface_summary,
                events=[entry.event for entry in group],
            )
        )

    manifest = TrackManifest(tracks=tracks)
    return PipelineResult(
        track_manifest=manifest,
        unresolved_unknowns=unresolved_unknowns,
        warnings=[],
    )
