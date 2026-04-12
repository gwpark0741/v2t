# Prototype Implementation Log

## Scope
This log covers the first approved prototype slice for `pipeline_context_v4.md`.

- Removed the timestamp grid snapping/checking helpers to align with the latest spec.
- Added deterministic track_id generation that normalizes surface keys before assignment.
- Implemented modules:
  - `models.py`
  - `validation.py`
  - `merge_rules.py`
  - `synthesizer.py`
  - focused tests under `tests/`

## Purpose / Input / Output

### `models.py`
- Purpose: define the canonical Pydantic schema layer for the prototype slice.
- Inputs: Python dictionaries or typed values for entities, actions, tracks, unresolved unknowns, warnings, and pipeline results.
- Outputs: validated Pydantic models used by the rest of the prototype.
- Dependency points: `validation.py`, `merge_rules.py`, `synthesizer.py`, tests.

### `validation.py`
- Purpose: enforce the current prototype invariants that do not require LLM or video processing.
- Inputs: events, `TrackManifest`, `PipelineResult`.
- Outputs: issue lists for manifest/pipeline consistency problems (duplicate track IDs, duplicate unresolved unknowns).
- Dependency points: `models.py`, tests.

### `merge_rules.py`
- Purpose: implement the current Agent C merge gate for approved actions.
- Inputs: two `Action` models and an optional `surface_compatibility` function.
- Outputs: `True` or `False` merge decision.
- Dependency points: `models.py`, `synthesizer.py`, tests.

### `synthesizer.py`
- Purpose: convert approved `Action` inputs into a `PipelineResult`.
- Inputs:
  - `list[Action]`
  - optional `surface_compatibility` callback
  - optional `source_entity_kind_by_id` mapping for `track_type`
- Outputs:
  - `PipelineResult`
    - `track_manifest`
    - `unresolved_unknowns`
    - `warnings`
- Dependency points: `models.py`, `merge_rules.py`, tests.
- Additional notes:
  - track_id is generated deterministically after grouping: background/electronic use `{source_entity_id}__{interaction_type}`, hard_effect/foley append a normalized surface_key derived from the representative surface_context_summary.
  - surface_summary가 없을 때는 action_id 및 event signature 기반으로 해시된 `missing_surface_*` suffix를 사용하여 동일한 source+interaction에서 split된 트랙의 deterministic ID 충돌을 막습니다.

## Environment Setup
- `uv sync`

## Review Status
- Reviewer approval obtained for this slice.
- Approved behavior:
  - unresolved unknown separation
  - `REASSIGN_TO_EXISTING` rewrite before merge
  - conservative `hard_effect` / `foley` surface gate
  - longest-string rule for `sound_description` and `surface_context_summary`

## Test Status
- Automated tests passed: `16/16`
- Status: passed
- The synthesizer-focused tests now assert the deterministic `track_id` outputs for background/electronic (no suffix), hard_effect/foley (normalized surface_key suffix), and missing-surface hash suffix uniqueness.

## Next Recommended Slice
- Final Validation warnings generation
- preprocessing/cut config helpers
- Agent A / Agent B interface contracts

## Preprocessing MVP Slice

### Scope
- Added the minimum preprocessing contract for the v4 pipeline inside `prototype`.
- This slice covers metadata extraction and authoritative cut generation only.
- It does not include ffmpeg clip generation, Gemini upload, Agent A/B orchestration, or final warnings.

### Purpose / Input / Output

### `models.py`
- Added `VideoMetadata`, `Cut`, and `PreprocessingResult`.
- Purpose: define the validated preprocessing output contract before any LLM stage runs.
- Inputs: extracted numeric video metadata and detector-derived cut boundaries.
- Outputs: typed models reused by preprocessing code and future orchestration layers.

### `preprocessing.py`
- Purpose: provide a single preprocessing entry point that reads video metadata and generates `Cut[]` with `AdaptiveDetector`.
- Inputs:
  - `video_path: Path`
  - optional detector params:
    - `adaptive_threshold=4.0`
    - `min_scene_len=30`
    - `window_width=2`
    - `min_content_val=15.0`
- Outputs:
  - `extract_video_metadata(...) -> VideoMetadata`
  - `detect_cuts(...) -> list[Cut]`
  - `run_preprocessing(...) -> PreprocessingResult`
- Dependency points:
  - `scenedetect[opencv]`
  - `models.py`
  - `tests/test_preprocessing.py`
- Additional notes:
  - authoritative cut boundaries come from `AdaptiveDetector`
  - cut IDs are generated deterministically as `CUT_001`, `CUT_002`, ...
  - timestamp grid snapping is not used
  - this MVP uses OpenCV-backed metadata extraction because `ffprobe` is not available in the current environment

### Environment Setup
- `uv add "scenedetect[opencv]>=0.6.7.1"`
- `uv sync`

### Review Status
- Reviewer approval obtained for the preprocessing MVP slice.
- Approved behavior:
  - minimal preprocessing-only scope
  - explicit I/O contract for metadata and cuts
  - v4 default `AdaptiveDetector` params
  - isolated preprocessing module export through package `__init__`

### Test Status
- Automated tests passed: `20/20`
- Preprocessing-specific tests passed: `4/4`
- Smoke check passed:
  - `run_preprocessing('../videos/02_playing_table_tennis__same_class_abab_5s.mp4')`
  - returned valid metadata plus `6` cuts
- Status: passed

### Updated Next Recommended Slice
- Segment Prep MVP (`ffmpeg` clip generation contract)
- Agent A input/output contract
- Agent B input/output contract

## Preprocessing HTML Report Slice

### Scope
- Added a minimal static HTML report generator for the preprocessing result only.
- This slice is limited to rendering `PreprocessingResult` into an easy-to-read file for manual inspection of cut segmentation.
- No thumbnails, JS, ffmpeg assets, or batch dashboards were added.

### Purpose / Input / Output

### `preprocessing_report.py`
- Purpose: convert one `PreprocessingResult` into a standalone HTML report.
- Inputs:
  - `result: PreprocessingResult`
  - optional `title: str | None`
  - optional `output_path: Path` for file writing
- Outputs:
  - `build_preprocessing_report_html(...) -> str`
  - `write_preprocessing_report(...) -> Path`
- Dependency points:
  - `models.py`
  - `preprocessing.py`
  - `tests/test_preprocessing_report.py`
- Additional notes:
  - report content is derived solely from `PreprocessingResult`
  - includes video metadata, cut count, a simple horizontal timeline, and a cut table
  - generated smoke-test artifact:
    - `reports/preprocessing_02_playing_table_tennis.html`

### Review Status
- Reviewer initially blocked one scope-creep issue:
  - detector defaults were being inferred from `run_preprocessing` instead of relying only on `PreprocessingResult`
- Developer removed that helper/card and the reviewer then approved.

### Test Status
- Automated tests passed: `tests/test_preprocessing_report.py -> 2/2`
- Demo artifact generation passed:
  - `run_preprocessing('../videos/02_playing_table_tennis__same_class_abab_5s.mp4')`
  - `write_preprocessing_report(...)`
  - generated `reports/preprocessing_02_playing_table_tennis.html`

### Updated Next Recommended Slice
- Segment Prep MVP (`ffmpeg` clip generation contract)
- Agent A input/output contract
- Agent B input/output contract
