# v2t Prototype Phase 0

This directory holds the Phase 0 foundation for the v4 video-to-sound prototype. It currently contains the uv-based package scaffold plus the schema and validation behaviors described in `pipeline_context_v4.md`.

Features implemented in this phase:

- Pydantic v2 models for the entity/action/track/pipeline hierarchy
- Validation helpers for manifest/pipeline consistency (duplicate track detection and unresolved unknown deduplication)
- Deterministic `track_id` generation that follows the latest spec (background/electronic omit surface suffix, hard_effect/foley append normalized surface_key)
- Focused tests covering the schema, validation helpers, merge rules, and synthesizer (including the deterministic `track_id` behavior)

Further phases will build on this foundation.
