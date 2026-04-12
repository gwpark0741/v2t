# Prototype Test Report

## Target
Approved prototype slice for:
- schema models
- validation helpers
- merge rules
- track synthesizer

## Environment
- Working directory: `prototype`
- Setup command: `uv sync`

## Automated Test Commands
- `uv run python -m pytest tests/test_models.py -q`
- `uv run python -m pytest tests/test_validation.py -q`
- `uv run python -m pytest tests/test_merge_rules.py -q`
- `uv run python -m pytest tests/test_synthesizer.py -q`
- `uv run python -m pytest tests -q`

## Automated Results
- `tests/test_models.py`: `4 passed`
- `tests/test_validation.py`: `2 passed`
- `tests/test_merge_rules.py`: `4 passed`
- `tests/test_synthesizer.py`: `6 passed`
- full suite: `16 passed, 0 warnings`

## Manual I/O Checks

### Models
- Valid `UNKNOWN_*` action with `unknown_resolution`: accepted
- `UNKNOWN_*` action without `unknown_resolution`: rejected
- event dict to `OnsetEvent` / `ContinuousEvent`: accepted
- empty `Track.events`: rejected

### Validation
- duplicate `track_id`: reported
- duplicate unresolved unknown: reported

### Merge Rules
- `hard_effect` same surface: merged
- `hard_effect` different surface: split
- `foley` missing surface: split
- `background`: merged regardless of surface text

### Synthesizer
- returns `PipelineResult`
- unresolved unknown actions move to `unresolved_unknowns`
- `REASSIGN_TO_EXISTING` rewrites source id before merge
- `track_type` depends on source kind mapping
- longest `sound_description` and longest non-null `surface_context` are selected
- deterministic `track_id` generation validated for background/electronic (no surface suffix) and hard_effect/foley (normalized surface_key suffix)
- deterministic `track_id` collision 방지를 위해 surface_summary가 없는 그룹은 action_id/event signature 기반 hash suffix를 붙여 서로 다른 ID를 만듭니다.

## Semantic Notes
- Current default surface comparator is exact-string based and intentionally conservative.
- Pydantic deprecation warning in `synthesizer.py` was resolved by switching to `model_copy()`.

## Final Judgment
- Passed
