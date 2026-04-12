# Pipeline Context: Video-to-Sound Metadata (Gemini-based)

> This document is a compressed handoff of ~20 rounds of design review.
> Use as the single source of truth for a new conversation.
> If anything here conflicts with older documents (v3, v3.1, v3.2, v3.3), prefer this.

---

## Goal

- Input: one silent video (10–60s)
- Output: `TrackManifest` JSON — structured sound-layer metadata for downstream T2A or sound recommendation
- Core principle: **track ≠ entity** — final grouping unit is `sound source / sound layer`

---

## Final Architecture (v3.3 + Addendum A + Hybrid Cut)

```
Video Input (10-60s, no audio)
  │
  ▼
[Preprocessing] ─── CODE
  ├─ ffprobe metadata
  ├─ AdaptiveDetector (pyscenedetect) → cut boundaries (확정)
  ├─ Cut[] 생성 (ID, start_time, end_time)
  └─ Gemini File API upload → file_uri
  │
  ▼
[Agent A: Global Video Analyzer] ─── LLM (gemini-2.5-pro)
  ├─ Receives pre-determined cuts (does NOT modify boundaries)
  ├─ Enriches each cut: camera_angle, transition_type, camera_notes
  ├─ Extracts: characters, objects, backgrounds, ambience_sources
  ├─ Sound-relevant entities only
  └─ Output: GlobalAnalysis
  │
  ▼
[Validation Gate] ─── CODE
  └─ Schema + timeline + linkage checks, error-type retry
  │
  ▼
[Segment Preparation] ─── CODE
  ├─ ffmpeg re-encode (-c:v libx264 -crf 18) per cut, ±1.0s padding
  ├─ Can run in parallel with Agent A (cuts already determined)
  └─ Upload segments to Gemini File API
  │
  ▼
[Agent B: Per-Cut Sound Analyst] ─── LLM (gemini-2.5-pro) × N fan-out
  ├─ One segment per call, max 5 concurrent
  ├─ Records: actions, observed descriptions, unknown entities
  └─ Output: CutSoundAnalysis per cut
  │
  ▼
[Reconciliation Gate] ─── CODE + conditional LLM (gemini-2.5-flash)
  ├─ 4 cases: unknown, low-confidence, description conflict, duplicate
  ├─ Code scores first (multi-dim), LLM only for ambiguous (0.50–0.85)
  └─ Output: corrected registry + corrected analyses
  │
  ▼
[Agent C: Track Synthesizer] ─── CODE + conditional LLM (gemini-2.5-flash)
  ├─ canonical_should_merge() handles most decisions
  ├─ LLM only for REVIEW cases (target: <10% of decisions)
  └─ Output: TrackManifest
  │
  ▼
[Final Validation] ─── CODE
  └─ 3-tier entity coverage, timeline, grid, warnings
  │
  ▼
PipelineResult (JSON)
```

---

## Design Evolution Summary

### v1 → v2: structural improvements
- Agent 1 (entity) + Agent 2 (cut) merged into single Agent A (saves one video upload, consistent context)
- track = sound source/layer (not entity)
- background vs ambience_source separated (1:N)
- UNKNOWN entity + reconciliation gate introduced
- 0.2s timestamp grid, entity audibility states introduced
- excluded_entities for visual_only/inactive

### v2 → v3: implementation hardening
- Agent B changed from full-video+time-instruction to real segment clipping
- Reconciliation expanded: unknown + low-confidence + description conflict + duplicate
- Agent B output gets sound_source, surface_context, track_hint fields
- boundary_flag for cut-edge events

### v3 → v3.1: schema precision
- likely_audible: 3-tier rule (audible=track required, likely_audible=track OR excluded required, visual_only=excluded required)
- Agent B gets observed_visual_description, observed_material, observed_surface
- Segment strategy: re-encode from day 1 (not copy mode)
- sound_source split into L1 (closed 13-category) + L2 (free-form)
- surface_compatible() with source-aware strictness

### v3.1 → v3.2: rule unification
- Single canonical_should_merge() — no duplicate merge logic
- Boundary L2 exception limited to continuous-nature L1 only
- Surface strictness: strict (footstep/impact/rolling/friction), normal (mechanism/deformation), loose (motor/vocal/gas/electronic/ambience)

### v3.2 → v3.3: implementation robustness
- canonical_should_merge() time-order independent (internal sort)
- Time adjacency: gap range -0.2 to 0.4 (allows 0.2s grid overlap)
- Boundary + different action_label → REVIEW (not auto-merge)
- extract_core_surface() rewritten with head-material extraction + object noun removal
- Segment.warnings + PipelineResult typed WarningItem

### v3.3 Addendum A: residual polish
- primary_source_id field added (merge uses this instead of linked_character + linked_object pair)
- Surface alias table + unknown fallback for generic LLM outputs
- All warnings upgraded to typed WarningItem(code, severity, message, context)

### Final decision: Hybrid cut detection
- pyscenedetect AdaptiveDetector determines cut boundaries (deterministic, stable)
- Agent A does NOT create or modify cuts — only enriches with camera_angle, transition_type, camera_notes
- Enables parallel execution: segment prep can start before Agent A returns
- Simplifies validation (no cut-boundary error retry path)

---

## Cut Detection Config

```json
{
  "detector": "AdaptiveDetector",
  "params": {
    "adaptive_threshold": 4,
    "min_scene_len": 30,
    "min_content_val": 15.0
  }
}
```

- adaptive_threshold 4: slightly conservative (default 3.0), avoids over-segmentation
- min_scene_len 30: ~1.25s at 24fps, prevents micro-cuts
- min_content_val 15.0: floor to ignore static/black frames
- Known limitation: slow dissolves (>2s) may be missed; tune threshold to 3.5 if needed

---

## Key Schemas (abbreviated)

### Entity types

- Character: id, label, visual_description, entry_exit_intervals, audibility, confidence
- Object: + material, surface, mechanism
- Background: visual place identity, linked_ambience_sources[]
- AmbienceSource: sound-layer identity, space_description, distance_profile, tonal_quality

### Audibility states

| State | Rule |
|-------|------|
| audible | Must be in track. Cannot be excluded. |
| likely_audible | Must be in track OR excluded (with reason). |
| visual_only / inactive | Must be excluded. Cannot be in track. |

### Action (Agent B output)

- primary_source_id: single entity that physically produces the sound
- sound_source_l1: closed 13-category (impact, friction, rolling, mechanism, motor, liquid, gas, footstep, body_movement, vocal, deformation, electronic, ambience_element)
- sound_source_l2: free-form sub-type
- surface_context, track_hint, boundary_flag
- observed_visual_description (independent from registry)

### Track (final output)

- track_id, track_type (sfx/ambience), track_label
- sound_source_l1/l2, entity_refs[], sound_description, surface_context_summary
- events[]: onset (timestamp) or continuous (start_time, end_time)
- confidence: {min, mean, support_count}

---

## Merge Logic: canonical_should_merge()

Single function, order-independent. Decision tree:

```
1. Internal time-sort (first, second)
2. Same L1?              → no: SPLIT
3. Same primary_source?  → no: SPLIT
4. Both continuous?      → no: SPLIT
5. Time adjacent?        → gap outside [-0.2, 0.4]: SPLIT
6. Surface compatible?   → source-aware strictness: SPLIT if incompatible
7. Same L2 + same label? → MERGE
8. Boundary + continuous-nature L1:
   - same label          → MERGE
   - different label     → REVIEW (state change like walking→running)
9. Same track_hint       → REVIEW (soft signal)
10. Otherwise            → SPLIT
```

### Surface strictness by L1

- strict: footstep, impact, rolling, friction (core material must match exactly)
- normal: mechanism, deformation, body_movement, liquid (same category OK)
- loose: motor, vocal, gas, electronic, ambience_element (surface irrelevant)

### Continuous vs discrete nature (for boundary exception)

- continuous-nature (boundary L2 exception allowed): footstep, body_movement, friction, rolling, motor, liquid, gas, ambience_element
- discrete-nature (no boundary exception): impact, mechanism, vocal, deformation, electronic

---

## LLM vs CODE Split

| Stage | Executor | LLM model | When LLM is called |
|-------|----------|-----------|-------------------|
| Preprocessing | CODE | — | never |
| Agent A | LLM | gemini-2.5-pro | always (1 call) |
| Validation Gate | CODE | — | never |
| Segment Prep | CODE | — | never |
| Agent B | LLM | gemini-2.5-pro | always (N calls, 1 per cut) |
| Reconciliation | CODE first | gemini-2.5-flash | only when score is 0.50–0.85 |
| Agent C | CODE first | gemini-2.5-flash | only for REVIEW merge decisions |
| Final Validation | CODE | — | never |

---

## Prompt Intent Summary

### Agent A
- Watch full video, receive pre-determined cut list
- Extract sound-relevant entities only (not static props)
- Separate background (visual) from ambience_sources (sound layers, 1:N)
- Enrich each cut with camera_angle, transition_type, camera_notes
- Do NOT modify cut boundaries
- All timestamps 0.2s grid

### Agent B (per segment)
- Analyze one segment (target range + padding context)
- Match entities from registry using exact IDs
- If not in registry → UNKNOWN_{TYPE}_CUT{NNN}_{SEQ}
- Write observed descriptions independently (don't copy registry)
- Every action: primary_source_id, L1, L2, surface_context, track_hint
- Events only within target range; boundary_flag for clamped events

### Reconciliation fallback (conditional)
- Resolve one ambiguous matching case
- Conservative: prefer SPLIT over false MERGE
- Actions: KEEP_CURRENT, REASSIGN_TO_EXISTING, CREATE_NEW, MERGE_WITH_EXISTING

### Agent C REVIEW (conditional)
- Two events, decide MERGE or SPLIT
- MERGE only if acoustically continuous
- If uncertain, SPLIT

---

## Cost Estimate

60s video, ~15 cuts: **~$0.46** total (well under $1 target)

| Stage | Calls | Model | Cost |
|-------|-------|-------|------|
| Agent A | 1 | pro | ~$0.15 |
| Agent B | 15 | pro | ~$0.25 |
| Reconciliation | 0-5 | flash | ~$0.02 |
| Agent C | 1 + reviews | flash | ~$0.03 |

Segment-based approach saves ~87% vs full-video-per-cut approach.

---

## Final Output: Text Description

TrackManifest → per-track text description for T2A:

```
{sound_description}. Surface: {surface_context_summary}. {events[0].description}. Duration: {computed}s
```

Example:
```
"Soft rubber sole footsteps on wet cobblestone, moderate walking pace, light puddle splashes on impact. Surface: wet irregular cobblestone with small puddles. Steady walking, consistent pace. Duration: 5.8s"
```

Each track becomes an independent T2A generation prompt. sfx tracks → individual sound events; ambience tracks → loopable background beds.

---

## Implementation Plan

### Phase 0: Infrastructure (1-2 days)
- Pydantic v2 schemas (all models)
- Gemini API wrapper (upload, generate with schema)
- ffmpeg/pyscenedetect utilities
- AdaptiveDetector config validation

### Phase 1: Agent A (2-3 days)
- Hypothesis: Gemini can extract entities + enrich pre-determined cuts in one call
- Success: entity recall >80%, cut enrichment coherent
- Failure: split entity extraction and cut enrichment into 2 calls

### Phase 2: Agent B + Segments (3-4 days)
- Hypothesis: segment-based fan-out produces consistent analysis
- Success: entity matching >85%, unknown <15%, L1 classification consistent
- Failure: increase padding, enforce closed L2 list

### Phase 3: Reconciliation + Agent C (2-3 days)
- Hypothesis: sound_source-based track separation is meaningful
- Success: multi-track per entity >50%, no over-segmentation (30+)
- Failure: replace Agent C with rule-based merge engine

### Phase 4: Integration (2-3 days)
- asyncio orchestration with parallel segment prep
- Error handling, retry, cost monitoring
- 10 video end-to-end test

---

## Open Tuning Parameters (decide during implementation)

| Parameter | Initial | Decide at |
|-----------|---------|-----------|
| DESCRIPTION_CONFLICT_THRESHOLD | 0.6 | Phase 2 |
| Surface category members | 6 categories | Phase 3 |
| max_concurrency | 5 | Phase 2 |
| Padding size | ±1.0s | Phase 2 |
| L1 taxonomy expansion | 13 categories | Phase 2-3 |
| REVIEW ratio threshold | 10% | Phase 3 |
| AdaptiveDetector adaptive_threshold | 4.0 | Phase 1 |

---

## Key Trade-offs Decided

| Decision | Chosen | Rejected | Rationale |
|----------|--------|----------|-----------|
| Agent 1+2 separate vs merged | Merged (Agent A) | Separate | 1 video upload, consistent context, MVP simplicity |
| Cut detection | pyscenedetect (deterministic) | Gemini (semantic) | Stability, reproducibility, parallel segment prep |
| Segment delivery | Re-encode clip | Full video + time instruction | Isolation, 87% cost reduction, frame-accurate timing |
| Track grouping | Sound source/layer | Entity-based | Same entity needs multiple tracks (footstep + cloth + breath) |
| Timestamp policy | 0.2s grid snap | Free precision | Gemini timestamp variance makes finer grid unreliable |
| likely_audible | Track OR excluded required (failure) | Warning only | Prevents judgment gaps without forcing hallucinated tracks |
| Surface comparison | Source-aware strictness | Uniform strictness | Footstep on cobblestone vs carpet matters; motor surface doesn't |
| Boundary merge exception | Continuous-nature L1 only | All L1 | mechanism/hinge vs latch_click should never auto-merge at boundary |
| Sound source taxonomy | L1 closed + L2 free | Single free field | L1 ensures cross-cut consistency; L2 preserves specificity |
| Merge entity check | primary_source_id | linked_char + linked_obj pair | Avoids false splits when cut records different secondary links |
| Warning schema | Typed WarningItem | Plain strings | Consistency with overall typed-schema philosophy |
