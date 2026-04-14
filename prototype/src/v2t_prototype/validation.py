from __future__ import annotations

from typing import List

from .models import PipelineResult, TrackManifest, Track


def validate_track_manifest_consistency(manifest: TrackManifest) -> List[str]:
    """Check high-level invariants for a track manifest."""
    issues: List[str] = []
    seen_ids: set[str] = set()
    for track in manifest.tracks:
        if track.track_id in seen_ids:
            issues.append(f"duplicate track_id {track.track_id}")
        else:
            seen_ids.add(track.track_id)
    return issues


def validate_pipeline_result(result: PipelineResult) -> List[str]:
    """Aggregate validations for the full pipeline result."""
    return validate_track_manifest_consistency(result.track_manifest)
