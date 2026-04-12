from __future__ import annotations

from .models import (
    Action,
    AmbienceSource,
    Cut,
    ContinuousEvent,
    InteractionType,
    OnsetEvent,
    PipelineResult,
    PreprocessingResult,
    Track,
    TrackManifest,
    TrackType,
    UnresolvedUnknown,
    UnknownResolution,
    VideoMetadata,
    WarningItem,
)
from .merge_rules import canonical_should_merge, default_surface_compatibility
from .preprocessing import detect_cuts, extract_video_metadata, run_preprocessing
from .preprocessing_report import build_preprocessing_report_html, write_preprocessing_report
from .synthesizer import synthesize_tracks

__all__ = [
    "Action",
    "AmbienceSource",
    "Cut",
    "ContinuousEvent",
    "InteractionType",
    "OnsetEvent",
    "PipelineResult",
    "PreprocessingResult",
    "Track",
    "TrackManifest",
    "TrackType",
    "UnresolvedUnknown",
    "UnknownResolution",
    "VideoMetadata",
    "WarningItem",
    "canonical_should_merge",
    "detect_cuts",
    "default_surface_compatibility",
    "extract_video_metadata",
    "run_preprocessing",
    "build_preprocessing_report_html",
    "write_preprocessing_report",
    "synthesize_tracks",
]
