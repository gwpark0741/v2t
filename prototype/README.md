# v2t Prototype

This directory holds the current prototype slices for the v4 video-to-sound pipeline. It currently contains the uv-based package scaffold, the schema/validation/Agent C core behaviors, and the preprocessing MVP described in `pipeline_context_v4.md`.

Features implemented in this phase:

- Pydantic v2 models for the entity/action/track/pipeline hierarchy
- Validation helpers for manifest/pipeline consistency (duplicate track detection and unresolved unknown deduplication)
- Deterministic `track_id` generation that follows the latest spec (background/electronic omit surface suffix, hard_effect/foley append normalized surface_key)
- Preprocessing MVP:
  - `VideoMetadata`, `Cut`, `PreprocessingResult`
  - `extract_video_metadata(...)`
  - `detect_cuts(...)`
  - `run_preprocessing(...)`
  - `AdaptiveDetector` defaults aligned to the v4 spec (`4.0 / 30 / 2 / 15.0`)
- Static preprocessing HTML reporting:
  - `build_preprocessing_report_html(...)`
  - `write_preprocessing_report(...)`
  - metadata/timeline/cut-table summary from `PreprocessingResult`
- Focused tests covering the schema, validation helpers, merge rules, and synthesizer (including the deterministic `track_id` behavior)
- Preprocessing tests covering synthetic-video metadata extraction, sequential cut labeling, and end-to-end preprocessing output

Further phases will build on this foundation.
