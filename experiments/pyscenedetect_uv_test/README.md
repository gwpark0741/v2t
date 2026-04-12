# PySceneDetect UV Test

This is a minimal, isolated experiment for testing cut detection with `pyscenedetect`
against videos stored in the repository-level `videos/` directory.

## Layout

- Code and uv environment: `experiments/pyscenedetect_uv_test/`
- Outputs: `outputs/pyscenedetect_uv_test/`
- Inputs: `videos/*.mp4`

## Setup

```bash
uv sync --python /Library/Developer/CommandLineTools/usr/bin/python3 --cache-dir ../../.uv-cache
```

## Run

Single video:

```bash
uv run --python /Library/Developer/CommandLineTools/usr/bin/python3 --cache-dir ../../.uv-cache python run_pyscenedetect_test.py --video ../../videos/01_people_crowd__people_sobbing_abab_5s.mp4
```

All mp4 videos in `videos/`:

```bash
uv run --python /Library/Developer/CommandLineTools/usr/bin/python3 --cache-dir ../../.uv-cache python run_pyscenedetect_test.py
```

More sensitive cut detection:

```bash
uv run --python /Library/Developer/CommandLineTools/usr/bin/python3 --cache-dir ../../.uv-cache python run_pyscenedetect_test.py --threshold 20 --min-scene-len 6
```

## Output

For each video, the script creates:

- `scene_list.csv`
- `cut_list.csv`
- `summary.json`

An aggregate `run_summary.json` is also written under `outputs/pyscenedetect_uv_test/`.
