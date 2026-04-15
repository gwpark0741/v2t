# Surface Merge Issue Context

This document summarizes the currently observed Stage 05/06 merge-quality issues based on real pipeline runs, with concrete artifact references and implementation implications.

Primary evidence used for this context:
- code under `prototype/src/v2t_prototype/`
- rerun of video 13:
  - `prototype/runs/manual_video13_rerun_20260414_134752/`
- demo run of video 07:
  - `prototype/runs/manual_video07_demo_20260414_140849/`

## Executive Summary

There are currently two different classes of Stage 06 merge problems:

1. `surface_context = null` acts as a universal compatibility bridge and over-merges acoustically distinct actions into one track.
2. non-null `surface_context` labels can drift across cuts for the same repeated sound source, causing over-splitting of what should be one continuous track.

These two failure modes pull in opposite directions:
- video 13 shows over-merge
- video 07 shows over-split

This means the current merge system is over-trusting `surface_context` in two inconsistent ways:
- `null` is treated as too permissive
- non-null strings are treated as too authoritative

## Current Code Behavior

### 1. Null surface is always marked compatible

In `prototype/src/v2t_prototype/surface_judge.py`:
- if both surfaces are null, result is `COMPATIBLE`
- if one side is null, result is also `COMPATIBLE` with source `null_one_side`

This is the direct cause of null-driven over-merge.

### 2. Merge grouping is transitive

In `prototype/src/v2t_prototype/synthesizer.py`, `build_merge_groups(...)`:
- creates one representative action per surface variant
- judges pairwise compatibility between variants
- unions all compatible variants with union-find

This means:
- A incompatible with B
- A compatible with null
- B compatible with null

can still end up in the same connected component.

### 3. Surface is currently a strong merge key

For same `source_entity_id + interaction_type`, Stage 06 still relies heavily on `surface_context` to decide grouping.
As a result:
- unstable surface guesses split tracks
- permissive null handling collapses tracks

## Problem Class A: Null-Driven Over-Merge

## Video 13 Evidence

Run:
- `prototype/runs/manual_video13_rerun_20260414_134752/`

### Case A1: `char_black_samurai__foley__lacquer_on_fabric`

Final track:
- `prototype/runs/manual_video13_rerun_20260414_134752/stage_06_agent_c/output.json`

This single track contains actions that originated from multiple incompatible surface families, including:
- `leather on metal`
- `skin on cloth`
- `metal on cloth`
- `lacquer on fabric`
- `metal on leather`
- plus `null`

Representative evidence from `surface_judgments.json`:
- `leather on metal` vs `skin on cloth` -> `INCOMPATIBLE`
- `metal on cloth` vs `lacquer on fabric` -> `INCOMPATIBLE`
- `lacquer on fabric` vs `metal on leather` -> `INCOMPATIBLE`
- but each of those families is also `COMPATIBLE` with `null`

Implication:
- null is functioning as a bridge between multiple mutually incompatible acoustic surface groups
- the final track label `lacquer_on_fabric` is misleading because the merged track contains more than that surface family

### Case A2: `obj_katana__foley__metal_on_fabric`

Final track:
- `prototype/runs/manual_video13_rerun_20260414_134752/stage_06_agent_c/output.json`

This track merges:
- `null`
- `metal on fabric`
- `metal on air`

Representative evidence:
- `null` vs `metal on fabric` -> `COMPATIBLE`
- `null` vs `metal on air` -> `COMPATIBLE`
- `metal on fabric` vs `metal on air` -> `INCOMPATIBLE`

Implication:
- sword air-whoosh and sword/fabric-related motion are being merged only because null bridges them

### Case A3: `char_red_samurai__foley__lacquer_on_fabric`

Final track:
- `prototype/runs/manual_video13_rerun_20260414_134752/stage_06_agent_c/output.json`

This track merges incompatible families such as:
- `metal on cloth`
- `lacquer on fabric`
- `metal on leather`
- `metal on fabric`
- plus `null`

Representative evidence:
- `metal on cloth` vs `lacquer on fabric` -> `INCOMPATIBLE`
- `metal on cloth` vs `metal on leather` -> `INCOMPATIBLE`
- `lacquer on fabric` vs `metal on fabric` -> `INCOMPATIBLE`
- yet `null` is compatible with each of them

Implication:
- same pattern as black samurai
- null is not just filling a missing label, it is merging otherwise separated surface groups

## Problem Class B: Null Merges Semantically Different Foley

## Video 07 Evidence

Run:
- `prototype/runs/manual_video07_demo_20260414_140849/`

### Case B1: singer voice merged into `cloth_on_cloth`

Stage 05 actions include:
- `CUT_002`: singer vocals, `surface_context = null`
- `CUT_003`: singer vocals, `surface_context = null`
- `CUT_003`: clothing rustle, `surface_context = cloth on cloth`
- `CUT_006`: singer vocals, `surface_context = null`

But Stage 06 produces:
- `char_singer__foley__cloth_on_cloth`

This means the final track labeled as `cloth_on_cloth` contains:
- actual singing voice
- clothing movement rustle

Representative evidence from `surface_judgments.json`:
- `act_CUT002_001 (null)` vs `act_CUT003_002 (cloth on cloth)` -> `COMPATIBLE`

Implication:
- even when null does not bridge multiple concrete families, it can still absorb semantically different foley events into one surface-labeled track
- voice-like null foley should not automatically join body/cloth surface groups

This is different from the video 13 problem:
- video 13 is multi-family transitive over-merge
- video 07 is semantic collapse of voice and clothing within one source/interaction bucket

## Problem Class C: Surface Drift Causes Over-Splitting

## Video 07 Evidence

### Case C1: train wheel sound split into two hard-effect tracks

Stage 05 produced:
- `CUT_001`: `obj_model_train`, `hard_effect`, `plastic on metal`
- `CUT_004`: `obj_model_train`, `hard_effect`, `metal on metal`

These are visually the same repeated event class:
- same model train
- same rolling-on-track action
- same source entity
- same interaction type
- near-identical sound role

Stage 06 judged them:
- `plastic on metal` vs `metal on metal` -> `INCOMPATIBLE`

Final result:
- `obj_model_train__hard_effect__plastic_on_metal`
- `obj_model_train__hard_effect__metal_on_metal`

Implication:
- cut-level material guessing drift causes false separation of one continuous repeated sound source
- the merge system currently trusts per-cut surface strings more than cross-cut source continuity

This is the opposite failure mode from the null issue:
- over-merge when null is too permissive
- over-split when surface strings are too strict

## Operational Note: Stage 05 Runtime Failure

In the video 07 demo run:
- `CUT_005` was not dropped by validation
- it failed because Agent B runtime received `503 UNAVAILABLE`

Evidence:
- `prototype/runs/manual_video07_demo_20260414_140849/stage_05_agent_b/output.json`

Implication:
- this is not a merge-quality issue
- it is a transient runtime retry gap
- current cut-level retry logic focuses on parse/validation retries, not transient API exceptions

## Root Cause Summary

Current behavior effectively assumes:

1. if surface is null, merge is safe
2. if surface is non-null and mismatched, split is safe

Real runs show both assumptions are false.

What the evidence suggests instead:

1. null means unknown, not universally compatible
2. surface strings are noisy hypotheses, not canonical truth
3. source continuity and event semantics sometimes matter more than surface strings

## Design Requirements for a Better Strategy

Any fix should satisfy all of the following:

1. null must not bridge multiple concrete surface families into one track
2. null foley must not automatically merge voice-like events into cloth/body-movement tracks
3. repeated same-source mechanical events should not split only because surface wording drifted
4. changes should remain conservative and easy to reason about in Stage 06

## Candidate Handling Strategy

### Strategy Layer 1: block null as a bridge

Recommended behavior:
- build concrete-surface groups first
- treat null actions as attachable only after concrete grouping
- allow a null action to attach to at most one concrete group
- if attachment is ambiguous, keep it in a null-only group

This directly addresses the video 13 over-merge.

### Strategy Layer 2: add semantic subtype gating for null foley

Recommended behavior for null foley:
- classify into a small subtype set such as:
  - `voice_vocal`
  - `voice_exertion`
  - `cloth_body_movement`
  - `weapon_motion`
  - `generic_unknown`

Then:
- do not attach null foley to a concrete surface group unless subtype is compatible

This directly addresses the video 07 singer case.

### Strategy Layer 3: reduce strictness for repeated same-source continuity

Recommended behavior:
- for same `source_entity_id + interaction_type`, if sound role and event pattern are highly similar across nearby cuts, allow a continuity override or weaker surface weighting
- especially for repetitive mechanical sources such as wheels, motors, rails, repeated prop motion

This directly addresses the video 07 train split case.

## Recommended Implementation Order

### Phase 1
- prevent null from bridging multiple concrete groups
- keep the logic local to Stage 06 merge grouping

Expected benefit:
- fixes the highest-confidence failure mode from video 13

### Phase 2
- add null-foley subtype gating

Expected benefit:
- prevents voice/body/cloth semantic collapse like the singer case in video 07

### Phase 3
- add continuity bias or soft matching for same-source repeated events

Expected benefit:
- reduces false surface-driven splits such as the train wheel example

## Validation Targets

The fix should be considered successful only if it improves all of the following:

### Video 13 target
- black samurai foley no longer collapses multiple incompatible surface families into one track
- red samurai foley no longer collapses multiple incompatible surface families into one track
- katana foley does not merge `metal on air` and `metal on fabric` through null bridging

### Video 07 target
- singer vocals do not merge into `cloth_on_cloth`
- model train rolling sound does not split only because one cut says `plastic on metal` and another says `metal on metal`

### Regression guard
- clearly different hard-effect surfaces should still remain separable when the sound source actually changes

## Bottom Line

The current Stage 06 merge behavior is not failing in only one direction.

It currently:
- over-merges when `null` is treated as universally compatible
- over-splits when noisy surface labels are treated as authoritative

The next implementation should therefore treat `surface_context` as useful evidence, but not as the sole truth source for grouping.
