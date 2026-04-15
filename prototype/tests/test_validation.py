from v2t_prototype.models import (
    OnsetEvent,
    Track,
    TrackManifest,
    PipelineResult,
    UnresolvedUnknown,
    WarningItem,
)
from v2t_prototype.validation import (
    validate_pipeline_result,
    validate_track_manifest_consistency,
)


def test_validate_track_manifest_consistency_reports_duplicates():
    track_a = Track(
        track_id="trk_dupe",
        track_type="sfx",
        source_entity_id="obj_001",
        interaction_type="sfx",
        sound_description="clink",
        events=[OnsetEvent(type="onset", timestamp=0.2)],
    )
    track_b = Track(
        track_id="trk_dupe",
        track_type="sfx",
        source_entity_id="obj_002",
        interaction_type="sfx",
        sound_description="clack",
        events=[OnsetEvent(type="onset", timestamp=0.4)],
    )
    issues = validate_track_manifest_consistency(TrackManifest(tracks=[track_a, track_b]))
    assert any("duplicate track_id" in issue for issue in issues)


def test_validate_pipeline_result_ignores_duplicate_unresolved_unknown_ids():
    track = Track(
        track_id="trk_uniq",
        track_type="sfx",
        source_entity_id="obj_003",
        interaction_type="sfx",
        sound_description="footsteps",
        events=[OnsetEvent(type="onset", timestamp=0.2)],
    )
    manifest = TrackManifest(tracks=[track])
    warning = WarningItem(code="W001", severity="info", message="ok", context={})
    unknown = UnresolvedUnknown(
        unknown_id="UNKNOWN_1",
        cut_id="CUT_001",
        observed_visual_description="strange tool",
        interaction_type="sfx",
        sound_description="clang",
    )
    result = PipelineResult(
        track_manifest=manifest,
        unresolved_unknowns=[unknown, unknown],
        warnings=[warning],
    )
    issues = validate_pipeline_result(result)
    assert issues == []
