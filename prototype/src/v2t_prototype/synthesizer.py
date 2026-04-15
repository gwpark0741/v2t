from __future__ import annotations

import hashlib
import re

from collections import defaultdict
from typing import Dict, List, Optional

from .models import (
    Action,
    PipelineResult,
    Track,
    TrackManifest,
    TrackType,
    UnresolvedUnknown,
)
from .track_judge import TrackJudge


def _is_unresolved_unknown(action: Action) -> bool:
    return (
        action.primary_source_id.startswith("UNKNOWN_")
        and action.unknown_resolution is not None
        and action.unknown_resolution.suggestion == "UNRESOLVED"
    )


def _normalize_source_id(action: Action) -> str:
    if (
        action.primary_source_id.startswith("UNKNOWN_")
        and action.unknown_resolution is not None
        and action.unknown_resolution.suggestion == "REASSIGN_TO_EXISTING"
        and action.unknown_resolution.suggested_entity_id
    ):
        return action.unknown_resolution.suggested_entity_id
    return action.primary_source_id


def _resolved_action(action: Action) -> Action:
    normalized_id = _normalize_source_id(action)
    if normalized_id == action.primary_source_id:
        return action
    return action.model_copy(update={"primary_source_id": normalized_id})


def _track_type_for(
    source_id: str,
    source_entity_kind_by_id: Optional[Dict[str, str]],
) -> TrackType:
    if source_entity_kind_by_id and source_entity_kind_by_id.get(source_id) == "AmbienceSource":
        return "ambience"
    return "sfx"


def normalize_key(text: str) -> str:
    normalized = text.lower().strip()
    normalized = re.sub(r"[^a-z0-9\s]", "", normalized)
    normalized = re.sub(r"\s+", "_", normalized)
    return normalized[:40] or "group"


def _stable_group_suffix(group_actions: list[Action]) -> str:
    sorted_ids = sorted(action.action_id for action in group_actions)
    material = "|".join(sorted_ids)
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:8]


def build_track_id(
    source_id: str,
    interaction_type: str,
    event_type: str,
    group_actions: list[Action],
    *,
    total_groups: int,
    used_ids: set[str],
) -> str:
    if interaction_type == "ambience":
        return f"{source_id}__ambience"

    base = f"{source_id}__sfx__{event_type}"
    if total_groups == 1:
        return base

    rep_desc = max(group_actions, key=lambda action: len(action.sound_description))
    desc_key = normalize_key(rep_desc.sound_description)
    candidate = f"{base}__{desc_key}"
    if candidate not in used_ids:
        return candidate
    return f"{candidate}__{_stable_group_suffix(group_actions)}"


def _sort_group_actions(group_actions: list[Action]) -> list[Action]:
    def _event_sort_key(action: Action) -> tuple[float, float]:
        event = action.event
        if hasattr(event, "timestamp"):
            return (float(event.timestamp), float(event.timestamp))
        return (float(event.start_time), float(event.end_time))

    return sorted(group_actions, key=lambda action: (_event_sort_key(action), action.action_id))


def synthesize_tracks(
    actions: List[Action],
    *,
    source_entity_kind_by_id: Optional[Dict[str, str]] = None,
    track_judge: Optional[TrackJudge] = None,
) -> PipelineResult:
    unresolved_unknowns: list[UnresolvedUnknown] = []
    buckets: dict[tuple[str, str, str], list[Action]] = defaultdict(list)

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
        buckets[(resolved.primary_source_id, resolved.interaction_type, resolved.event.type)].append(resolved)

    tracks: list[Track] = []
    used_track_ids: set[str] = set()

    for (source_id, interaction_type, event_type), bucket_actions in buckets.items():
        if interaction_type == "ambience":
            grouped_actions = [list(bucket_actions)]
        elif track_judge is not None:
            grouped_actions = track_judge.judge_group(
                list(bucket_actions),
                source_id=source_id,
                interaction_type=interaction_type,
                event_type=event_type,
            )
        else:
            grouped_actions = [[action] for action in bucket_actions]

        total_groups = len(grouped_actions)
        for group_actions in grouped_actions:
            ordered_group = _sort_group_actions(group_actions)
            sound_choice = max(ordered_group, key=lambda action: len(action.sound_description))
            track_id = build_track_id(
                source_id,
                interaction_type,
                event_type,
                ordered_group,
                total_groups=total_groups,
                used_ids=used_track_ids,
            )
            used_track_ids.add(track_id)
            tracks.append(
                Track(
                    track_id=track_id,
                    track_type=_track_type_for(source_id, source_entity_kind_by_id),
                    source_entity_id=source_id,
                    interaction_type=interaction_type,
                    sound_description=sound_choice.sound_description,
                    events=[action.event for action in ordered_group],
                )
            )

    return PipelineResult(
        track_manifest=TrackManifest(tracks=tracks),
        unresolved_unknowns=unresolved_unknowns,
        warnings=[],
    )
