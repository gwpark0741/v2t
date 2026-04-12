# Claude Handoff Context: Gemini Video-to-Sound Pipeline

## 1. Purpose

This document is the consolidated handoff context for the current Gemini-based video-to-sound metadata pipeline design.

The goal is:

- Input: one silent video, typically 10 to 60 seconds
- Output: a structured `TrackManifest` for downstream sound recommendation / T2A generation
- Core idea: `track != entity`; the final unit is `sound source / sound layer`

This document merges and resolves the following source docs:

- `pipeline_v3_architecture.md`
- `pipeline_v3_1_patch.md`
- `pipeline_v3_2_patch.md`
- `pipeline_v3_3_patch.md`

Patch precedence:

1. `v3`
2. `v3.1`
3. `v3.2`
4. `v3.3`
5. `v3.3 Addendum A`

If two docs conflict, the later patch wins.

## 2. Final Design Decisions

These are the current ground-truth decisions.

- Agent A and Agent B are the main LLM video-analysis stages.
- Validation, segment preparation, most merge logic, and final consistency checks are code-driven.
- Reconciliation and Agent C REVIEW cases are text-only LLM fallback steps, not primary stages.
- Agent B uses real cut segments with padding, not `full video + time instruction`.
- Segment clipping uses re-encode from the start of the project, not `-c copy`.
- Agent A and Agent B use different FPS settings:
  - Agent A: `videoMetadata.fps = 2`
  - Agent B: `videoMetadata.fps = 5`
- All timestamps are snapped to a 0.2-second grid.
- Cut definition is shot-boundary based.
- Small composition or angle changes inside one shot are recorded as metadata, not separate cuts.
- Track grouping is based on sound source / layer, not just character/object identity.
- `background` and `ambience_source` are separate concepts:
  - `background`: visual place identity
  - `ambience_source`: sound-layer identity
- `likely_audible` is not optional:
  - it must end up either in a track or in `excluded_entities`
  - if neither happens, validation fails
- Agent C merge uses `primary_source_id`, not raw exact-match of `linked_character_id + linked_object_id`
- Warning objects should use a typed schema, not raw strings

## 3. End-to-End Pipeline

### Step 0. Preprocessing

This stage is code-only.

Input:

- original video file

Process:

- read metadata with `ffprobe`
- upload the full original video once to Gemini File API
- run `pyscenedetect` to produce shot-boundary candidates

Output:

- `video_metadata`
- `full_video_file_uri`
- `scene_change_candidates`

### Step 1. Agent A: Global Video Analyzer

Model:

- `gemini-2.5-pro`

Input:

- full video `file_uri`
- video metadata
- scene change candidates

Responsibilities:

- watch the entire video once
- build global registries for:
  - characters
  - sound-relevant objects
  - backgrounds
  - ambience sources
- detect shot-boundary cuts
- assign stable IDs
- determine initial `audibility`
- use `videoMetadata.fps = 2` for global analysis

Output:

- `GlobalAnalysis`

### Step 2. Validation Gate

This stage is code-only.

Checks:

- schema validity
- cut continuity
- timeline sanity
- background / ambience linkage consistency
- duplicate or malformed IDs
- Agent A timestamps are validated as coarse global structure timestamps, not fine sync timestamps
- Agent A outputs must still conform to the shared 0.2-second schema, but should not be treated as sub-0.5s-precise timing ground truth

Output:

- validated `GlobalAnalysis`
- retry or failure if invalid

### Step 3. Segment Preparation

This stage is code-only.

Input:

- original video
- cut list from Agent A

Process:

- for each cut, create a real segment with `+-1.0s` padding
- use `ffmpeg` re-encode:
  - `-c:v libx264`
  - `-crf 18`
  - `-an`
- upload each segment to Gemini File API
- trust requested `seg_start` because re-encode is frame-accurate enough
- validate duration drift

Output:

- `Segment[]`

### Step 4. Agent B: Per-Cut Sound Analyst

Model:

- `gemini-2.5-pro`

Input:

- segment `file_uri`
- target cut range
- entity registry
- ambience registry
- cut metadata

Responsibilities:

- analyze one cut at a time using segment plus padding context
- identify entities present in this cut
- record observed descriptions, not copied registry descriptions
- produce cut-level actions and ambience observations
- output structured sound-source metadata for downstream grouping
- use `videoMetadata.fps = 5` for cut-level timing and action analysis

Output:

- `CutSoundAnalysis`

### Step 5. Reconciliation Gate

This stage is hybrid:

- first-pass scoring by code
- ambiguous cases only -> `gemini-2.5-flash`

Cases handled:

- unknown entities
- low-confidence matches
- description conflicts
- same-cut duplicate matches

Responsibilities:

- fix wrong assignments
- promote real new entities when needed
- prevent over-merge
- treat Agent B cut-level observations as the primary evidence for local timing and local appearance
- use Agent A timestamps mainly as coarse global windows and ID anchors

Output:

- reconciled registries
- updated cut analyses

### Step 6. Agent C: Track Synthesizer

Primary behavior:

- code-driven merge and split using `canonical_should_merge()`

Fallback:

- `gemini-2.5-flash` only for `REVIEW` pairs

Responsibilities:

- convert cut-level actions into final sound-layer tracks
- aggregate events across cuts
- keep acoustically distinct layers separate

Output:

- `TrackManifest`

### Step 7. Final Validation

This stage is code-only.

Checks:

- schema validity
- entity coverage
- excluded entity correctness
- timestamp grid compliance
- onset / continuous validity
- track consistency
- warning aggregation
- Agent B action timestamps are the authoritative timing source for final track events
- Agent A timestamps are validated for structural consistency and containment, not fine-grained sync accuracy

Output:

- `PipelineResult`

## 4. High-Level I/O Flow

```text
video.mp4
  -> Preprocessing
  -> Agent A
  -> GlobalAnalysis
  -> Validation Gate
  -> Segment Preparation
  -> Segment[]
  -> Agent B fan-out
  -> CutSoundAnalysis[]
  -> Reconciliation Gate
  -> reconciled CutSoundAnalysis[]
  -> Agent C
  -> TrackManifest
  -> Final Validation
  -> PipelineResult
```

## 5. Core Schemas

### 5.1 GlobalAnalysis

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
- `entry_exit_intervals[]`
- `global_action_summary`
- `audibility`
- `confidence`
- note: entry/exit intervals here are coarse global-analysis intervals from Agent A (`fps=2`), normalized to the shared schema

Object fields:

- `id`
- `label`
- `visual_description`
- `material`
- `surface`
- `mechanism`
- `entry_exit_intervals[]`
- `audibility`
- `confidence`
- note: entry/exit intervals here are coarse global-analysis intervals from Agent A (`fps=2`), normalized to the shared schema

Background fields:

- `id`
- `label`
- `visual_description`
- `linked_ambience_sources[]`

Ambience source fields:

- `id`
- `label`
- `category`
- `space_description`
- `distance_profile`
- `linked_background_id`

Cut fields:

- `id`
- `start_time`
- `end_time`
- `camera_angle`
- `transition_in`
- `transition_out`
- `camera_notes`
- `confidence`
- note: cut start/end are global-analysis cut boundaries from Agent A (`fps=2`) and are not the source of truth for fine action timing

### 5.2 Segment

Contains:

- `cut_id`
- `file_path`
- `file_uri`
- `actual_seg_start`
- `actual_seg_end`
- `target_start`
- `target_end`
- `padding_before`
- `padding_after`
- `timing_drift`
- `warnings[]`

### 5.3 CutSoundAnalysis

Contains:

- `cut_id`
- `characters_present[]`
- `objects_present[]`
- `ambience_sources_present[]`
- `unknown_entities[]`
- `actions[]`
- `ambience_observations[]`

Character presence fields:

- `entity_id`
- `match_confidence`
- `confidence_flag`
- `observed_visual_description`
- `observed_action_summary`

Object presence fields:

- `entity_id`
- `match_confidence`
- `confidence_flag`
- `observed_visual_description`
- `observed_material`
- `observed_surface`

Action fields:

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
- note: action timestamps here come from Agent B (`fps=5`) and are the timing source of truth for downstream track events

Unknown entity fields:

- `temp_id`
- `type`
- `label`
- `visual_description`
- `material`
- `surface`
- `reason`

### 5.4 TrackManifest / PipelineResult

Track fields:

- `track_id`
- `track_type`
- `track_label`
- `sound_source_l1`
- `sound_source_l2`
- `entity_refs[]`
- `sound_description`
- `events[]`
- `confidence`

Track confidence fields:

- `min`
- `mean`
- `support_count`

Excluded entity fields:

- `entity_id`
- `label`
- `audibility`
- `reason`

Warning fields:

- `code`
- `severity`
- `message`
- `context`

Pipeline result fields:

- `status`
- `tracks[]`
- `excluded_entities[]`
- `unresolved_cuts[]`
- `unresolved_entities[]`
- `warnings[]`
- `cost_summary`

## 6. Sound Source Taxonomy

`sound_source_l1` is a closed list:

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

`sound_source_l2` is free-form, but should stay consistent inside the same video.

Examples:

- `mechanism` -> `hinge_rotation`
- `mechanism` -> `latch_click`
- `footstep` -> `heel_strike_on_stone`
- `impact` -> `door_slam`

## 7. Timestamp Policy

- All timestamps are absolute timestamps in the original video timeline.
- All timestamps are snapped to a 0.2-second grid.
- Agent A runs at `2 FPS`, so its timestamps are coarse global-analysis timestamps normalized to the shared 0.2-second schema.
- Agent B runs at `5 FPS`, so the 0.2-second grid is the meaningful timing resolution for cut-level action annotation.
- Agent A timestamps are used for:
  - coarse cut boundaries
  - entity entry/exit windows
  - global registry indexing
- Agent B timestamps are used for:
  - onset timing
  - continuous action boundaries
  - final track event timing
- If Agent A and Agent B imply different fine timing, prefer Agent B for action/event timing and keep Agent A as coarse structural metadata.
- `onset` events use exactly one `timestamp`.
- `continuous` events use exactly `start_time` and `end_time`.
- Segment padding exists only for context.
- Final recorded events must stay inside the target cut range.
- Boundary-spanning continuous events are clamped and marked with `boundary_flag=true`.

## 8. Final Validation Policy

### Entity coverage

- `audible`
  - must appear in at least one track
  - must not appear in `excluded_entities`
- `likely_audible`
  - must appear in a track, or
  - must appear in `excluded_entities` with a reason
- `visual_only` or `inactive`
  - must appear in `excluded_entities`
  - must not appear in a track

### Structural rules

- all timestamps must be on the 0.2-second grid
- Agent A timestamps are checked for schema normalization and coarse consistency only
- Agent B / Track timestamps are checked as fine timing outputs
- all `continuous` events must satisfy `start_time < end_time`
- onset timestamps must fall within valid visibility / activity ranges using Agent A intervals as coarse windows, not frame-accurate limits
- track events must be time-ordered
- no invalid continuous overlap inside one track
- final track event timing should be derived from Agent B actions, not directly from Agent A cut or entity timestamps

## 9. Merge Logic

The final merge logic is code-driven.

The only canonical rule is `canonical_should_merge()`.

Important final behavior:

- normalize event order internally by time
- require same `sound_source_l1`
- require same `primary_source_id`
- require both events to be `continuous`
- require time adjacency:
  - `gap = second.start_time - first.end_time`
  - valid range: `-0.2 <= gap <= 0.4`
- require `surface_compatible(...)`

Then:

- same `sound_source_l2` and same `action_label` -> `MERGE`
- boundary case + continuous-nature L1 + same label -> `MERGE`
- boundary case + same L1 but different label -> `REVIEW`
- same `track_hint` may trigger `REVIEW`
- otherwise -> `SPLIT`

Important scope note:

- merge decisions operate on Agent B action events
- Agent A timestamps are not merge inputs except as upstream structural context

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

## 10. Surface Normalization and Compatibility

Surface comparison is source-aware.

Strictness by L1:

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

1. direct alias match
2. remove modifiers like `wet`, `dry`, `rough`, `rusted`
3. remove object nouns like `window`, `hinge`, `panel`, `door`
4. extract the remaining core surface/material
5. if generic, use alias/fallback mapping

Representative aliases:

- `hard floor` -> `hardwood`
- `stone ground` -> `stone`
- `metal part` -> `metal`
- `metal surface` -> `metal`
- `wet surface` -> `wet stone`
- `hard surface` -> `concrete`
- `soft surface` -> `carpet`
- `wooden floor` -> `hardwood`
- `glass surface` -> `glass`

Important note:

- `primary_source_id` matters more than full linked-entity equality
- for object-driven sounds, the object should usually be the primary source

## 11. Prompt Examples

The following are representative prompt templates, not exact SDK code.

### 11.1 Agent A system prompt

```text
You are an expert sound designer analyzing a silent video for downstream sound recommendation and track planning.

Watch the entire video before responding.
Use only visually observable evidence.
Return JSON only.

Core goals:
1. Build global registries for sound-relevant characters, objects, backgrounds, and ambience sources.
2. Detect shot-boundary cuts across the full video.

Hard rules:
- Register only sound-relevant entities.
- Do not invent names or off-screen causes.
- Use stable IDs:
  - characters: CHAR_001, CHAR_002, ...
  - objects: OBJ_001, OBJ_002, ...
  - backgrounds: BG_001, BG_002, ...
  - ambience_sources: AMB_001, AMB_002, ...
  - cuts: CUT_001, CUT_002, ...
- Separate visual background from ambience source.
- Use shot boundary for cut detection.
- Do not split a cut for small composition or angle changes inside the same shot; record those in camera_notes.
- Snap all timestamps to a 0.2-second grid.
- This request uses videoMetadata.fps=2 for global analysis.
- Your timestamps in this pass are coarse structural timestamps, not fine-grained action-sync timestamps.
- If uncertain, keep the description generic and lower confidence.
```

### 11.2 Agent A user prompt

```text
Analyze this full silent video.

Video metadata:
- duration: {duration_seconds}
- fps: {fps}
- resolution: {resolution}
- scene_change_candidates: {scene_change_candidates}

Tasks:
1. Track all sound-relevant characters across the full video.
2. Track all sound-relevant objects across the full video.
3. Identify visual backgrounds and separate ambience sources.
4. Detect all shot-boundary cuts.
5. Record timestamps on a 0.2-second grid.

Return JSON only matching the GlobalAnalysis schema.
```

### 11.3 Agent B system prompt

```text
You are a per-cut sound analyst for a silent video.

You are given a video segment that contains one target cut plus front/back padding.
Use the padding only for context.
Record events only inside the target analysis range.
Return JSON only.

Hard rules:
- Reuse the provided entity IDs exactly.
- If a visible entity cannot be matched confidently, use UNKNOWN_* in unknown_entities.
- Do not copy registry descriptions. Write what is actually visible in this segment.
- All timestamps must be absolute timestamps in the original video timeline.
- Snap all timestamps to a 0.2-second grid.
- This request uses videoMetadata.fps=5 for cut-level timing analysis.
- In this pass, action timestamps are the authoritative fine timing for downstream track generation.
- Use only visually observable evidence.

Entity matching:
- For each matched character/object, provide observed_visual_description from this segment.
- For objects, also provide observed_material and observed_surface if visible.
- If registry and observation differ, record the observed version and lower match_confidence.

Action rules:
- Every action must include:
  - linked_character_id
  - linked_object_id
  - primary_source_id
  - action_label
  - sound_source_l1
  - sound_source_l2
  - surface_context
  - track_hint
  - sfx_description
  - confidence
- primary_source_id means the physical source of the sound.
- onset -> one timestamp only
- continuous -> start_time and end_time only
- If a continuous action crosses the target boundary, clamp it and set boundary_flag=true.
- If an onset happens only in padding outside the target range, do not record it.

sound_source_l1 must be exactly one of:
impact, friction, rolling, mechanism, motor, liquid, gas, footstep, body_movement, vocal, deformation, electronic, ambience_element
```

### 11.4 Agent B user prompt

```text
This video segment is part of a larger original video.

Segment info:
- cut_id: {cut_id}
- segment absolute range in original video: {actual_seg_start:.1f}s to {actual_seg_end:.1f}s
- segment total duration: {segment_duration:.1f}s
- front padding: {padding_before:.1f}s
- back padding: {padding_after:.1f}s
- target analysis range in original video: {target_start:.1f}s to {target_end:.1f}s
- target analysis range inside this segment: {target_inner_start:.1f}s to {target_inner_end:.1f}s after segment start

Use content outside the target analysis range only for context.
Record actions only inside the target analysis range.
All timestamps must be written in absolute original-video time.

Cut metadata:
{cut_metadata_json}

Entity registry:
{entity_registry_json}

Ambience registry:
{ambience_registry_json}

Return JSON only matching the CutSoundAnalysis schema.
```

### 11.5 Reconciliation fallback prompt

```text
You are resolving an entity-matching ambiguity in a silent-video sound metadata pipeline.

Return JSON only.
Be conservative.
Prefer avoiding false merges.

Rules:
- Use only the provided candidates and cut context.
- Do not invent entities unless the correct decision is CREATE_NEW.
- If evidence is weak, prefer keeping entities separate.
- Use visual similarity, time overlap, material/surface consistency, and background consistency.

Resolve this case:
- case_type: {case_type}
- observed cut context: {cut_context_json}
- observed entity: {observed_entity_json}
- current matched entity: {current_match_json_or_null}
- candidate entities: {candidate_entities_json}

Choose one:
- KEEP_CURRENT
- REASSIGN_TO_EXISTING
- MERGE_WITH_EXISTING
- CREATE_NEW
- SPLIT_EXISTING

Return:
{
  "decision": "...",
  "target_entity_id": "..." | null,
  "confidence": 0.0,
  "rationale": "short explanation"
}
```

### 11.6 Agent C REVIEW prompt

```text
You are deciding whether two cut-level sound events belong to the same final sound track.

Return JSON only.

Decision goal:
- MERGE only if both events should be designed as one continuous sound layer.
- SPLIT if they should be separate sound layers.
- If uncertain, choose SPLIT.

Rules:
- Judge by acoustic continuity, not just semantic similarity.
- Consider:
  - primary_source_id
  - sound_source_l1
  - sound_source_l2
  - action_label
  - surface_context
  - boundary_flag
  - timing adjacency
  - sfx_description
- track_hint is only a soft signal.

Review this pair:
- review_reason: {review_reason}
- event_a: {event_a_json}
- event_b: {event_b_json}

Return:
{
  "decision": "MERGE" | "SPLIT",
  "confidence": 0.0,
  "rationale": "short explanation",
  "suggested_track_label_if_merge": "..." | null
}
```

## 12. Code vs LLM Responsibility Split

Code handles:

- preprocessing
- metadata extraction
- scene candidate detection
- validation
- segment preparation
- timestamp clamp
- reconciliation scoring
- most merge logic
- final validation
- warning aggregation

LLM handles:

- Agent A global video understanding
- Agent B per-cut semantic and sound-aware annotation
- reconciliation fallback for ambiguous cases only
- Agent C REVIEW for ambiguous merge cases only

## 13. Example Input / Output Flow

Example:

- Input video: 12-second silent clip
- Agent A detects:
  - `CHAR_001`
  - `OBJ_001`
  - `BG_001`, `BG_002`
  - `AMB_001`, `AMB_002`, `AMB_003`
  - `CUT_001` to `CUT_004`
- Segment prep creates one padded segment per cut
- Agent B outputs one `CutSoundAnalysis` per cut
- Reconciliation fixes ambiguous object matches
- Agent C builds separate tracks such as:
  - `CHAR_001 footsteps on wet cobblestone`
  - `CHAR_001 footsteps on tile`
  - `OBJ_001 hinge rotation`
  - `AMB_001 distant traffic`
- Final validation checks entity coverage, timestamps, and warnings

## 14. Known Residual Risks

These are not blockers, but likely tuning areas.

- threshold tuning for `description_conflict`
- expansion of `SURFACE_ALIASES`
- tuning REVIEW rate below 10 percent
- cut padding size tuning
- concurrency tuning
- L2 taxonomy normalization consistency

## 15. What I Want Claude To Do

Please treat this document as the current unified design spec.

I want you to:

- understand this pipeline as the current intended architecture
- review it for implementation consistency
- identify any remaining contradictions or hidden assumptions
- comment on whether the prompt design and I/O contracts are sufficient
- suggest the smallest implementation plan that preserves the core design

Important:

- Prefer the unified decisions in this document over earlier conflicting wording from the source docs.
- If you find a remaining ambiguity, call it out explicitly rather than silently choosing one.
