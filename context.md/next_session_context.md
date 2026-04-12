# Next Session Context (2026-04-12)

> Use this as the handoff for the next conversation.
> Read [`pipeline_compressed_context.md`](/Users/gwangwoong.park/workspace/v2t/pipeline_compressed_context.md) first for the architecture/design truth.
> Then read this file for the latest implementation state, experiment results, and open follow-ups.

---

## 1. Current Direction

- Final pipeline direction remains:
  - deterministic cut boundaries from `PySceneDetect`
  - Gemini does **not** author boundaries in the production architecture
  - Gemini enriches/analyzes cuts downstream
- `track != entity` is still the governing principle for the sound pipeline.
- For cut detection, the design target is now clearly:
  - `AdaptiveDetector`
  - stable, reproducible preprocessing
  - slightly over-segmented is acceptable, but under-segmentation is riskier

---

## 2. Important Repo State

### Architecture / planning docs

- Main architecture handoff:
  - [`pipeline_compressed_context.md`](/Users/gwangwoong.park/workspace/v2t/pipeline_compressed_context.md)
- Scene detector spec:
  - [`scene_detection_spec.md`](/Users/gwangwoong.park/workspace/v2t/scene_detection_spec.md)

### Experiment code

- Main comparison runner:
  - [`experiments/cut_compare_uv_test/run_cut_compare.py`](/Users/gwangwoong.park/workspace/v2t/experiments/cut_compare_uv_test/run_cut_compare.py)
- Stability runner:
  - [`experiments/cut_compare_uv_test/run_cut_stability.py`](/Users/gwangwoong.park/workspace/v2t/experiments/cut_compare_uv_test/run_cut_stability.py)
- Timing runner:
  - [`experiments/cut_compare_uv_test/run_cut_timing_compare.py`](/Users/gwangwoong.park/workspace/v2t/experiments/cut_compare_uv_test/run_cut_timing_compare.py)
- Adaptive threshold sweep runner:
  - [`experiments/cut_compare_uv_test/run_adaptive_threshold_sweep.py`](/Users/gwangwoong.park/workspace/v2t/experiments/cut_compare_uv_test/run_adaptive_threshold_sweep.py)

---

## 3. What Was Actually Implemented

### `run_cut_compare.py`

- `PySceneDetect` baseline was switched from `ContentDetector` to `AdaptiveDetector`.
- Current defaults in this script:
  - `adaptive_threshold=3.5`
  - `min_scene_len=30`
  - `window_width=2`
  - `min_content_val=15.0`
- Reports now display AdaptiveDetector params.
- Gemini retry logic was added earlier to handle truncated / non-JSON responses:
  - higher `maxOutputTokens`
  - compact fallback retry prompt

### `run_cut_timing_compare.py`

- Updated to use AdaptiveDetector params instead of old ContentDetector defaults.
- Measures:
  - `PySceneDetect only`
  - `Gemini only`
  - `Gemini pipeline total` = PySceneDetect candidate generation + Gemini call/parse

### `run_adaptive_threshold_sweep.py`

- New script added.
- Runs threshold sweep over:
  - `3.5`
  - `4.0`
  - `5.0`
- For each threshold:
  - runs 5 target videos (`10~14`)
  - generates clips
  - generates threshold-specific pairwise HTML report
- Also generates one combined threshold-sweep pairwise report and summary JSON.

---

## 4. Important Caveat

- Older outputs under:
  - [`outputs/cut_compare_uv_test/...`](/Users/gwangwoong.park/workspace/v2t/outputs/cut_compare_uv_test)
  - [`outputs/cut_compare_stability_uv_test/...`](/Users/gwangwoong.park/workspace/v2t/outputs/cut_compare_stability_uv_test)
- were produced **before** the AdaptiveDetector migration.
- Those older runs are useful for historical comparison, but they are **legacy ContentDetector-based experiments** and should not be treated as the final detector-tuning result.

Also:

- [`experiments/cut_compare_uv_test/run_cut_stability.py`](/Users/gwangwoong.park/workspace/v2t/experiments/cut_compare_uv_test/run_cut_stability.py)
  still has legacy threshold semantics/defaults and was **not fully updated** after the AdaptiveDetector migration.
- Before rerunning stability on the new detector, this script should be aligned with:
  - `adaptive_threshold`
  - `window_width`
  - `min_content_val`
  - updated help text/defaults

---

## 5. Key Experiment Outputs

### A. Legacy comparison run (ContentDetector-era)

- Combined pairwise report:
  - [`outputs/cut_compare_uv_test/20260411_203803/pairwise_comparison_report.html`](/Users/gwangwoong.park/workspace/v2t/outputs/cut_compare_uv_test/20260411_203803/pairwise_comparison_report.html)
- Legacy differing-only:
  - [`outputs/cut_compare_uv_test/20260411_203803/pairwise_differing_report.html`](/Users/gwangwoong.park/workspace/v2t/outputs/cut_compare_uv_test/20260411_203803/pairwise_differing_report.html)

### B. Legacy 3-run stability experiment (ContentDetector-era)

- Stability report:
  - [`outputs/cut_compare_stability_uv_test/20260411_211312/pairwise_stability_report.html`](/Users/gwangwoong.park/workspace/v2t/outputs/cut_compare_stability_uv_test/20260411_211312/pairwise_stability_report.html)
- Unstable-only:
  - [`outputs/cut_compare_stability_uv_test/20260411_211312/pairwise_stability_differing_report.html`](/Users/gwangwoong.park/workspace/v2t/outputs/cut_compare_stability_uv_test/20260411_211312/pairwise_stability_differing_report.html)
- Raw summary:
  - [`outputs/cut_compare_stability_uv_test/20260411_211312/stability_summary.json`](/Users/gwangwoong.park/workspace/v2t/outputs/cut_compare_stability_uv_test/20260411_211312/stability_summary.json)

### C. Timing comparison

- Timing report:
  - [`outputs/cut_timing_compare_uv_test/20260411_213346/timing_report.html`](/Users/gwangwoong.park/workspace/v2t/outputs/cut_timing_compare_uv_test/20260411_213346/timing_report.html)
- Timing summary:
  - [`outputs/cut_timing_compare_uv_test/20260411_213346/timing_summary.json`](/Users/gwangwoong.park/workspace/v2t/outputs/cut_timing_compare_uv_test/20260411_213346/timing_summary.json)

### D. Adaptive threshold sweep (current recommended reference)

- Combined threshold sweep pairwise report:
  - [`outputs/adaptive_threshold_sweep_uv_test/20260411_231255/threshold_sweep_pairwise_report.html`](/Users/gwangwoong.park/workspace/v2t/outputs/adaptive_threshold_sweep_uv_test/20260411_231255/threshold_sweep_pairwise_report.html)
- Combined summary JSON:
  - [`outputs/adaptive_threshold_sweep_uv_test/20260411_231255/threshold_sweep_summary.json`](/Users/gwangwoong.park/workspace/v2t/outputs/adaptive_threshold_sweep_uv_test/20260411_231255/threshold_sweep_summary.json)
- Threshold-specific reports:
  - [`outputs/adaptive_threshold_sweep_uv_test/20260411_231255/threshold_3_5/outputs/20260411_231256/pairwise_comparison_report.html`](/Users/gwangwoong.park/workspace/v2t/outputs/adaptive_threshold_sweep_uv_test/20260411_231255/threshold_3_5/outputs/20260411_231256/pairwise_comparison_report.html)
  - [`outputs/adaptive_threshold_sweep_uv_test/20260411_231255/threshold_4/outputs/20260411_231717/pairwise_comparison_report.html`](/Users/gwangwoong.park/workspace/v2t/outputs/adaptive_threshold_sweep_uv_test/20260411_231255/threshold_4/outputs/20260411_231717/pairwise_comparison_report.html)
  - [`outputs/adaptive_threshold_sweep_uv_test/20260411_231255/threshold_5/outputs/20260411_232124/pairwise_comparison_report.html`](/Users/gwangwoong.park/workspace/v2t/outputs/adaptive_threshold_sweep_uv_test/20260411_231255/threshold_5/outputs/20260411_232124/pairwise_comparison_report.html)

---

## 6. Key Findings

### A. Gemini runtime vs PySceneDetect

From the timing run:

- `PySceneDetect total`: `1.866s`
- `Gemini only total`: `223.057s`
- `Gemini pipeline total`: `224.922s`
- Average per video:
  - `PySceneDetect`: `0.373s`
  - `Gemini only`: `44.611s`
  - `Gemini pipeline`: `44.984s`

Interpretation:

- Gemini is roughly `120x` slower than PySceneDetect in this setup.
- This strongly supports the architectural decision:
  - use PySceneDetect for deterministic cut generation
  - do not depend on Gemini for primary boundary generation in production

### B. Legacy stability findings (ContentDetector-era, but still informative)

- PySceneDetect was fully deterministic across 3 runs:
  - exact-match pairs `15/15`
  - pairwise F1 `1.0`
- Gemini was unstable across repeated runs:
  - exact-match pairs `3/15`
  - pairwise F1 mean `~0.809`
  - unstable video rate `100%`

Interpretation:

- Gemini cut generation behaves like a semantic but non-deterministic segmenter.
- Good for analysis/enrichment, bad as the final authoritative cut source.

### C. AdaptiveDetector threshold sweep findings

Threshold summary:

| Threshold | Avg PySceneDetect Cuts | Avg Gemini Cuts | Avg F1 | Differing Videos |
|----------|-------------------------|-----------------|--------|------------------|
| 3.5 | 7.2 | 5.8 | 0.8600 | 2 |
| 4.0 | 6.8 | 6.4 | 0.9033 | 2 |
| 5.0 | 5.6 | 6.0 | 0.9333 | 1 |

Main read:

- Increasing `adaptive_threshold` reduced PySceneDetect cut density:
  - `7.2 -> 6.8 -> 5.6`
- Agreement with Gemini improved overall:
  - `F1 0.8600 -> 0.9033 -> 0.9333`
- Differing videos decreased:
  - `2 -> 2 -> 1`

But the per-video behavior matters:

- `10_sea_waves...`
  - stable across all thresholds
  - PySceneDetect `6/6/6`
  - Gemini `6/6/6`
  - F1 always `1.0`

- `11_fireworks...`
  - strongest improvement from raising threshold
  - PySceneDetect `10 -> 8 -> 6`
  - Gemini `4 -> 4 -> 6`
  - F1 `0.5 -> 0.6 -> 1.0`
  - clear evidence that AdaptiveDetector threshold helps suppress over-segmentation on high-motion / bursty scenes

- `12_tapping_guitar...`
  - stable and unchanged
  - PySceneDetect `4/4/4`
  - Gemini `4/4/4`
  - F1 always `1.0`

- `13_sword_fighting`
  - moderate sensitivity
  - PySceneDetect `12 -> 12 -> 9`
  - Gemini `12 -> 14 -> 9`
  - F1 `1.0 -> 0.9167 -> 1.0`
  - threshold `4.0` was slightly worse here due to Gemini splitting more finely

- `14_wood_fighting`
  - cautionary case
  - PySceneDetect `4 -> 4 -> 3`
  - Gemini `3 -> 4 -> 5`
  - F1 `0.8 -> 1.0 -> 0.6667`
  - `5.0` appears too aggressive here and may under-segment on the deterministic side

### D. Recommendation from the sweep

If choosing one default today:

- `adaptive_threshold = 4.0` is the safest operating default

Why not `5.0`?

- `5.0` has the best average F1, but it shows under-segmentation risk on `14_wood_fighting`
- The project preference is:
  - slight over-segmentation is acceptable
  - under-segmentation is riskier

So the practical interpretation is:

- default: `4.0`
- aggressive anti-oversegmentation option: `5.0`
- lower fallback for recall-sensitive cases: `3.5`

This matches the current architecture doc, which already leaned toward `AdaptiveDetector threshold 4`.

---

## 7. What To Treat As “Current Truth”

### Architecture truth

- [`pipeline_compressed_context.md`](/Users/gwangwoong.park/workspace/v2t/pipeline_compressed_context.md)

### Detector tuning truth

- [`outputs/adaptive_threshold_sweep_uv_test/20260411_231255/threshold_sweep_summary.json`](/Users/gwangwoong.park/workspace/v2t/outputs/adaptive_threshold_sweep_uv_test/20260411_231255/threshold_sweep_summary.json)
- Recommended operational default: `AdaptiveDetector(adaptive_threshold=4.0, min_scene_len=30, window_width=2, min_content_val=15.0)`

---

## 8. Open Follow-ups

1. Update `run_cut_stability.py` to the AdaptiveDetector parameter model.
   - It still reflects the old ContentDetector-style defaults/help text.

2. Re-run 3x stability using AdaptiveDetector.
   - Ideally do this for `3.5`, `4.0`, `5.0`.
   - Then compare:
     - stability of PySceneDetect under threshold changes
     - Gemini agreement under the new cut candidates

3. Integrate the chosen detector config into the real preprocessing stage of the main pipeline.
   - The design doc already assumes deterministic AdaptiveDetector cuts.
   - The codebase should converge on that same config.

4. If Gemini is ever reconsidered for cut generation:
   - constrain it to choose from candidate boundaries only, or
   - run multiple passes and majority-vote
   - otherwise its non-determinism remains a problem

---

## 9. Quick Open Commands

Open main architecture handoff:

```bash
open /Users/gwangwoong.park/workspace/v2t/pipeline_compressed_context.md
```

Open detector spec:

```bash
open /Users/gwangwoong.park/workspace/v2t/scene_detection_spec.md
```

Open current threshold sweep report:

```bash
open /Users/gwangwoong.park/workspace/v2t/outputs/adaptive_threshold_sweep_uv_test/20260411_231255/threshold_sweep_pairwise_report.html
```

Open timing report:

```bash
open /Users/gwangwoong.park/workspace/v2t/outputs/cut_timing_compare_uv_test/20260411_213346/timing_report.html
```

