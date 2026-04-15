from __future__ import annotations

import hashlib
import re

from collections import defaultdict
from typing import Dict, List, Optional

from .models import (
    Action,
    PipelineResult,
    Track,
    TrackGroupResult,
    TrackManifest,
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


def _track_type_sort_key(track_type: str) -> tuple[int, str]:
    order = {
        "sfx": 0,
        "voice": 1,
        "ambience": 2,
    }
    return (order.get(track_type, 99), track_type)


def normalize_key(text: str, *, max_length: int | None = 40) -> str:
    normalized = text.lower().strip()
    normalized = re.sub(r"[^a-z0-9\s]", "", normalized)
    normalized = re.sub(r"\s+", "_", normalized)
    if max_length is not None:
        normalized = normalized[:max_length]
    return normalized or "group"


def _stable_group_suffix(group_actions: list[Action]) -> str:
    sorted_ids = sorted(action.action_id for action in group_actions)
    material = "|".join(sorted_ids)
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:8]


def _group_key(source_id: str, interaction_type: str, event_type: str) -> str:
    return f"{source_id}__{interaction_type}__{event_type}"


def _group_by_normalized_description(actions: list[Action]) -> list[list[Action]]:
    grouped: dict[str, list[Action]] = defaultdict(list)
    ordered_keys: list[str] = []
    for action in actions:
        normalized = normalize_key(action.sound_description, max_length=None)
        if normalized not in grouped:
            ordered_keys.append(normalized)
        grouped[normalized].append(action)
    return [grouped[key] for key in ordered_keys]


def _representative_actions(groups: list[list[Action]]) -> list[Action]:
    representatives: list[Action] = []
    for group in groups:
        ordered = _sort_group_actions(group)
        representatives.append(ordered[0])
    return representatives


def _expand_group_results(
    representative_groups: list[list[Action]],
    deterministic_groups_by_rep_id: dict[str, list[Action]],
    reasons: list[str],
) -> tuple[list[list[Action]], list[TrackGroupResult]]:
    expanded_groups: list[list[Action]] = []
    expanded_results: list[TrackGroupResult] = []
    for rep_group, reason in zip(representative_groups, reasons):
        expanded_actions: list[Action] = []
        for representative in rep_group:
            expanded_actions.extend(deterministic_groups_by_rep_id[representative.action_id])
        ordered_actions = _sort_group_actions(expanded_actions)
        expanded_groups.append(ordered_actions)
        expanded_results.append(
            TrackGroupResult(
                action_ids=[action.action_id for action in ordered_actions],
                reason=reason,
            )
        )
    return expanded_groups, expanded_results


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

    base = f"{source_id}__{interaction_type}__{event_type}"
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


def _sort_tracks_for_manifest(tracks: list[Track]) -> list[Track]:
    return sorted(
        tracks,
        key=lambda track: (
            _track_type_sort_key(track.track_type),
            track.source_entity_id,
            track.track_id,
        ),
    )


def synthesize_tracks(
    actions: List[Action],
    *,
    source_entity_kind_by_id: Optional[Dict[str, str]] = None,
    track_judge: Optional[TrackJudge] = None,
) -> PipelineResult:
    _ = source_entity_kind_by_id
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
        group_key = _group_key(source_id, interaction_type, event_type)
        if interaction_type == "ambience":
            grouped_actions = [list(bucket_actions)]
            grouped_results = [
                TrackGroupResult(
                    action_ids=[action.action_id for action in _sort_group_actions(bucket_actions)],
                    reason="single ambience bucket",
                )
            ]
        elif track_judge is not None:
            deterministic_groups = _group_by_normalized_description(list(bucket_actions))
            if len(deterministic_groups) == 1:
                grouped_actions = [_sort_group_actions(deterministic_groups[0])]
                grouped_results = [
                    TrackGroupResult(
                        action_ids=[action.action_id for action in grouped_actions[0]],
                        reason="same normalized sound_description",
                    )
                ]
                track_judge.record_judgment(
                    group_key,
                    list(bucket_actions),
                    grouped_results,
                    source="deterministic",
                    model=None,
                )
            else:
                rep_actions = _representative_actions(deterministic_groups)
                decision = track_judge.judge_group_decision(
                    rep_actions,
                    source_id=source_id,
                    interaction_type=interaction_type,
                    event_type=event_type,
                    record=False,
                )
                deterministic_groups_by_rep_id = {
                    representative.action_id: _sort_group_actions(group)
                    for representative, group in zip(rep_actions, deterministic_groups)
                }
                grouped_actions, grouped_results = _expand_group_results(
                    decision.groups,
                    deterministic_groups_by_rep_id,
                    [group.reason for group in decision.group_results],
                )
                track_judge.record_judgment(
                    group_key,
                    list(bucket_actions),
                    grouped_results,
                    source=decision.source,
                    model=decision.model,
                )
        else:
            grouped_actions = [_sort_group_actions(group) for group in _group_by_normalized_description(list(bucket_actions))]

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
                    track_number=1,
                    track_id=track_id,
                    track_type=interaction_type,
                    source_entity_id=source_id,
                    sound_description=sound_choice.sound_description,
                    events=[action.event for action in ordered_group],
                )
            )

    numbered_tracks = [
        track.model_copy(update={"track_number": index})
        for index, track in enumerate(_sort_tracks_for_manifest(tracks), start=1)
    ]

    return PipelineResult(
        track_manifest=TrackManifest(tracks=numbered_tracks),
        unresolved_unknowns=unresolved_unknowns,
        warnings=[],
    )
