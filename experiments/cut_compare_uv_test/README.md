# Cut Compare UV Test

This experiment compares two cut segmentation outputs on the same input videos:

- `pyscenedetect`
- `Gemini 2.5 Pro`

The setup is intentionally isolated:

- code and environment: `experiments/cut_compare_uv_test/`
- outputs: `outputs/cut_compare_uv_test/`
- inputs: `videos/*.mp4`

The Gemini side uses the current Agent A cut-segmentation intent:

- full-video analysis
- `videoMetadata.fps = 2`
- shot-boundary cuts
- timestamps snapped to `0.2s`
- JSON-only output
