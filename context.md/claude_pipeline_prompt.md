# Claude Prompt: Current Video-to-Sound Pipeline Context

You are reviewing and helping implement a Gemini-based pipeline that converts a silent video into structured sound-design metadata and final sound-layer tracks.

Treat this document as the current source of truth.
If it conflicts with older notes, prefer this document.

## Goal

- Input: one silent video, typically 10 to 60 seconds
- Output: structured `TrackManifest` / `PipelineResult` for downstream sound recommendation or T2A
- Core principle: `track != entity`
- Final grouping unit: `sound source / sound layer`

## Source Priority

This prompt is a compressed merge of:

1. `pipeline_v3_architecture.md`
2. `pipeline_v3_1_patch.md`
3. `pipeline_v3_2_patch.md`
4. `pipeline_v3_3_patch.md`

Conflict rule:
- later patch wins
- `v3.3 + Addendum A` is the latest priority

## Fixed FPS Policy

- Agent A uses `videoMetadata.fps = 2`
- Agent B uses `videoMetadata.fps = 5`
- Agent A is for global structure and coarse timing
- Agent B is for cut-level action timing and 0.2-second event annotation

## Final Architecture

```text
Video Input
  -> Preprocessing
  -> Agent A: Global Video Analyzer
  -> Validation Gate
  -> Segment Preparation
  -> Agent B: Per-Cut Sound Analyst (fan-out)
  -> Reconciliation Gate
  -> Agent C: Track Synthesizer
  -> Final Validation
  -> PipelineResult
```

## Stage Responsibilities

### 1. Preprocessing

Code-only.

Does:
- `ffprobe` metadata extraction
- full video upload to Gemini File API
- shot-boundary candidate detection via `pyscenedetect`

Outputs:
- `video_metadata`
- `full_video_file_uri`
- `scene_change_candidates`

### 2. Agent A: Global Video Analyzer

Model:
- `gemini-2.5-pro`

Input:
- full video `file_uri`
- metadata
- scene candidates

Does:
- watches full video once
- extracts:
  - `characters`
  - `objects`
  - `backgrounds`
  - `ambience_sources`
  - `cuts`
- assigns stable IDs
- sets initial `audibility`
- uses `videoMetadata.fps = 2` for global analysis

Important rules:
- only collect sound-relevant entities
- cut definition is shot-boundary based
- small angle/composition changes stay inside one cut and go to `camera_notes`
- timestamps use 0.2s grid

### 3. Validation Gate

Code-only.

Checks:
- schema validity
- timeline sanity
- cut continuity
- background / ambience linkage consistency
- Agent A timestamps are validated as coarse structural timestamps, not fine sync timestamps

### 4. Segment Preparation

Code-only.

Input:
- original video
- cuts from Agent A

Does:
- create one real segment per cut
- includes `+-1.0s` padding
- uses `ffmpeg` re-encode from the beginning:
  - `-c:v libx264`
  - `-crf 18`
  - `-an`
- uploads each segment to Gemini File API
- trusts `seg_start` because re-encode is frame-accurate enough
- validates duration drift

### 5. Agent B: Per-Cut Sound Analyst

Model:
- `gemini-2.5-pro`

Input:
- segment `file_uri`
- target cut range
- entity registry
- ambience registry
- cut metadata

Does:
- analyzes one cut using segment + padding context
- records actual observed descriptions, not copied registry text
- uses `videoMetadata.fps = 5` for cut-level timing and action analysis
- outputs:
  - `characters_present`
  - `objects_present`
  - `ambience_sources_present`
  - `unknown_entities`
  - `actions`
  - `ambience_observations`

### 6. Reconciliation Gate

Hybrid:
- code scoring first
- ambiguous cases only -> `gemini-2.5-flash`

Handles:
- unknown entities
- low-confidence matches
- description conflicts
- same-cut duplicate matches

Timing interpretation:
- prefer Agent B for local timing evidence
- use Agent A timing mainly as coarse global windows and ID anchors

### 7. Agent C: Track Synthesizer

Mostly code-driven.

Fallback:
- `gemini-2.5-flash` only for `REVIEW` merge cases

Does:
- groups cut-level actions into final sound-layer tracks
- preserves acoustically distinct layers

### 8. Final Validation

Code-only.

Checks:
- entity coverage
- excluded entity correctness
- timestamp grid
- onset / continuous validity
- track consistency
- warning aggregation
- Agent B action timestamps are the timing source of truth for final track events
- Agent A timestamps are validated for structure and containment, not fine-grained sync

## Core Modeling Decisions

### Track model

- final track unit is a sound layer, not just a character or object
- examples:
  - same character can produce multiple tracks:
    - footsteps
    - cloth rustle
    - breath
  - same object can produce multiple tracks:
    - hinge rotation
    - latch click
    - slam impact

### Background vs ambience

- `background`: visual place identity
- `ambience_source`: sound-layer identity

### Audibility policy

- `audible`
  - must be in a track
  - cannot be in `excluded_entities`
- `likely_audible`
  - must be in a track or in `excluded_entities`
- `visual_only` / `inactive`
  - must be in `excluded_entities`
  - cannot be in tracks

### Timestamp policy

- all timestamps are absolute original-video timestamps
- all timestamps snap to `0.2s`
- Agent A uses `2 FPS`, so its timestamps are coarse global-analysis timestamps normalized to the shared schema
- Agent B uses `5 FPS`, so the `0.2s` grid is the practical timing resolution for cut-level actions
- Agent A timestamps are used for coarse cut boundaries, entry/exit windows, and global indexing
- Agent B timestamps are used for onset timing, continuous bounds, and final track event timing
- if Agent A and Agent B differ on fine timing, prefer Agent B for event timing
- `onset` -> single `timestamp`
- `continuous` -> `start_time`, `end_time`
- padding exists only for context
- events must be recorded only in the target cut range
- boundary-spanning continuous events are clamped and marked `boundary_flag=true`

## Main Schemas

### GlobalAnalysis

Contains:
- `characters[]`
- `objects[]`
- `backgrounds[]`
- `ambience_sources[]`
- `cuts[]`

Character fields:
- `id`
- `label`
- `visual_description`
- `entry_exit_intervals`
- `global_action_summary`
- `audibility`
- `confidence`

Object fields:
- `id`
- `label`
- `visual_description`
- `material`
- `surface`
- `mechanism`
- `entry_exit_intervals`
- `audibility`
- `confidence`

Cut fields:
- `id`
- `start_time`
- `end_time`
- `camera_angle`
- `transition_in`
- `transition_out`
- `camera_notes`
- `confidence`

### CutSoundAnalysis

Contains:
- `cut_id`
- `characters_present[]`
- `objects_present[]`
- `ambience_sources_present[]`
- `unknown_entities[]`
- `actions[]`
- `ambience_observations[]`

Important presence fields:
- `entity_id`
- `match_confidence`
- `confidence_flag`
- `observed_visual_description`
- for objects:
  - `observed_material`
  - `observed_surface`

Important action fields:
- `action_id`
- `linked_character_id`
- `linked_object_id`
- `primary_source_id`
- `action_label`
- `sound_source_l1`
- `sound_source_l2`
- `surface_context`
- `track_hint`
- `event_type`
- `timestamp`
- `start_time`
- `end_time`
- `boundary_flag`
- `sfx_description`
- `confidence`
- note: these timestamps come from Agent B (`fps=5`) and are the authoritative timing source downstream

### TrackManifest / PipelineResult

Track fields:
- `track_id`
- `track_type`
- `track_label`
- `sound_source_l1`
- `sound_source_l2`
- `entity_refs`
- `sound_description`
- `events`
- `confidence`

Track confidence fields:
- `min`
- `mean`
- `support_count`

Warning fields:
- `code`
- `severity`
- `message`
- `context`

## Sound Source Taxonomy

`sound_source_l1` is closed:

- `impact`
- `friction`
- `rolling`
- `mechanism`
- `motor`
- `liquid`
- `gas`
- `footstep`
- `body_movement`
- `vocal`
- `deformation`
- `electronic`
- `ambience_element`

`sound_source_l2` is free-form but should stay internally consistent.

Examples:
- `mechanism -> hinge_rotation`
- `mechanism -> latch_click`
- `footstep -> heel_strike_on_stone`
- `impact -> door_slam`

## Merge Logic

The only canonical merge rule is `canonical_should_merge()`.

Final behavior:

- normalize two events by time order internally
- require same `sound_source_l1`
- require same `primary_source_id`
- require both events to be `continuous`
- require time adjacency:
  - `gap = second.start_time - first.end_time`
  - valid range: `-0.2 <= gap <= 0.4`
- require surface compatibility

Then:
- same `sound_source_l2` and same `action_label` -> `MERGE`
- boundary case + continuous-nature L1 + same label -> `MERGE`
- boundary case + different label -> `REVIEW`
- same `track_hint` may trigger `REVIEW`
- otherwise -> `SPLIT`

Scope note:
- merge operates on Agent B action events
- Agent A timestamps are upstream structural context, not merge-time fine timing inputs

Continuous-nature L1:
- `footstep`
- `body_movement`
- `friction`
- `rolling`
- `motor`
- `liquid`
- `gas`
- `ambience_element`

Discrete-nature L1:
- `impact`
- `mechanism`
- `vocal`
- `deformation`
- `electronic`

## Surface Normalization

Surface compatibility is source-aware.

Strictness:
- strict:
  - `footstep`
  - `impact`
  - `rolling`
  - `friction`
- normal:
  - `mechanism`
  - `deformation`
  - `body_movement`
  - `liquid`
- loose:
  - `motor`
  - `vocal`
  - `gas`
  - `electronic`
  - `ambience_element`

Normalization strategy:

1. alias direct match
2. remove modifiers like `wet`, `dry`, `rough`, `rusted`
3. remove object nouns like `window`, `hinge`, `panel`, `door`
4. extract the remaining material / core surface
5. use alias fallback for generic phrases

Representative aliases:
- `hard floor -> hardwood`
- `stone ground -> stone`
- `metal part -> metal`
- `metal surface -> metal`
- `wet surface -> wet stone`
- `hard surface -> concrete`
- `soft surface -> carpet`

## Prompt Examples

These are representative prompt shapes, not exact SDK code.

### Agent A prompt intent

Tell Gemini:
- watch the full silent video
- use `videoMetadata.fps=2`
- register only sound-relevant entities
- separate backgrounds from ambience sources
- detect shot-boundary cuts
- use stable IDs
- use 0.2s timestamps
- treat timestamps in this pass as coarse structural timestamps
- return JSON only

### Agent B prompt intent

Tell Gemini:
- this segment contains one target cut plus padding
- use `videoMetadata.fps=5`
- padding is context only
- record events only inside the target range
- reuse provided IDs exactly
- if uncertain, emit `UNKNOWN_*`
- do not copy registry descriptions
- record observed descriptions from this segment
- every action must include:
  - `primary_source_id`
  - `sound_source_l1`
  - `sound_source_l2`
  - `surface_context`
  - `track_hint`
- all timestamps are absolute and snapped to 0.2s
- action timestamps in this pass are the authoritative fine timing for downstream tracks
- return JSON only

### Reconciliation fallback prompt intent

Tell Gemini:
- resolve one ambiguous entity-matching case
- be conservative
- avoid false merges
- choose among:
  - `KEEP_CURRENT`
  - `REASSIGN_TO_EXISTING`
  - `MERGE_WITH_EXISTING`
  - `CREATE_NEW`
  - `SPLIT_EXISTING`
- return JSON only

### Agent C REVIEW prompt intent

Tell Gemini:
- decide whether two events belong to the same final sound track
- `MERGE` only if acoustically continuous
- otherwise `SPLIT`
- if uncertain, choose `SPLIT`
- consider:
  - `primary_source_id`
  - `sound_source_l1`
  - `sound_source_l2`
  - `action_label`
  - `surface_context`
  - `boundary_flag`
  - `timing adjacency`
  - `sfx_description`
- return JSON only

## Code vs LLM Split

Code handles:
- preprocessing
- metadata extraction
- cut candidate detection
- validation
- segment prep
- timestamp clamp
- reconciliation scoring
- most merge logic
- warning aggregation

LLM handles:
- Agent A global video understanding
- Agent B cut-level sound-aware annotation
- reconciliation fallback for ambiguous cases only
- Agent C REVIEW for ambiguous merge cases only

## What I Want You To Do

Please use this as the current implementation context.

I want you to:

- review the architecture for consistency
- identify any remaining contradictions or hidden assumptions
- judge whether the prompt design and I/O contracts are sufficient
- suggest the smallest implementation plan that preserves the design

Important:
- if you find ambiguity, call it out explicitly
- do not silently prefer older notes over this prompt
