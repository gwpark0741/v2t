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
