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
  - `run_preprocessing(...)` (uploads the full video via Gemini Files API and returns `video_url` plus `video_mime_type`)
  - `AdaptiveDetector` defaults aligned to the v4 spec (`4.0 / 30 / 2 / 15.0`)
- Static preprocessing HTML reporting:
  - `build_preprocessing_report_html(...)`
  - `write_preprocessing_report(...)`
  - metadata/timeline/cut-table summary from `PreprocessingResult`
- Agent A HTML reporting:
  - `build_agent_a_report_html(...)`
  - `write_agent_a_report(...)`
  - video player plus metadata, registry, cut enrichments, and validation summary
- Agent A contract module:
  - `AgentARequest`, `EntityRegistry`, `AgentAResponse`
  - `build_agent_a_request(...)`
  - `validate_agent_a_response(...)` for cut linkage and registry ID consistency
- Agent A Gemini runtime module:
  - `create_gemini_client(...)`
  - `upload_video_file(...)`
  - `wait_for_uploaded_file_active(...)`
  - `run_agent_a_runtime(...)`
  - structured output parsing via `AgentAResponse.model_validate_json(response.text)`
  - post-validation via `validate_agent_a_response(...)`
- Focused tests covering the schema, validation helpers, merge rules, and synthesizer (including the deterministic `track_id` behavior)
- Preprocessing tests covering synthetic-video metadata extraction, sequential cut labeling, and end-to-end preprocessing output

Further phases will build on this foundation.

## Agent A Runtime Slice

This slice adds a real Gemini Developer API path for Agent A while keeping the
existing `agent_a.py` contract/validator module unchanged.

- Upload path:
- `run_preprocessing(...)` uploads the full video via Gemini Files API and records `video_mime_type`
- `run_agent_a_runtime(...)` consumes both `video_url` and the precomputed `video_mime_type`
- Generation path:
  - model: `gemini-2.5-pro`
  - config: `response_mime_type=application/json` and `response_json_schema`
- Parse/validate path:
  - parse: `AgentAResponse.model_validate_json(response.text)`
  - post-validation: `validate_agent_a_response(...)`

### Tiny Live Smoke Procedure (Manual Env Loading)

Do not rely on terminal auto env injection.
Load environment variables manually in shell, then run one small smoke call:

```bash
cd prototype
if [ -f ../.env ]; then set -a; source ../.env; set +a; fi
uv run python - <<'PY'
from pathlib import Path
from v2t_prototype.preprocessing import run_preprocessing
from v2t_prototype.agent_a_runtime import run_agent_a_runtime

video_path = Path('../videos/02_playing_table_tennis__same_class_abab_5s.mp4')
preprocessing = run_preprocessing(video_path)
result = run_agent_a_runtime(preprocessing=preprocessing, local_video_path=video_path)
print(result.request.video_url)
print(len(result.response.cut_enrichments))
PY
```

### Latest Smoke Status

One real live smoke succeeded outside the sandbox after the `ACTIVE`-wait fix.

- input: `../videos/02_playing_table_tennis__same_class_abab_5s.mp4`
- observed output summary:
  - uploaded `video_url` present
  - `characters`: `3`
  - `key_objects`: `4`
  - `ambience_sources`: `2`
  - `cut_enrichments`: `6`
- note:
  - sandboxed environments may still block DNS/network even when the runtime code is correct
