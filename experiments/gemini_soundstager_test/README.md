# Gemini Soundstager Experiment

This experiment keeps the setup intentionally small:

1. Read the target prompt from `experiments/prompts/soundstager.txt`.
2. Send the prompt plus one or more local videos to Gemini.
3. Save the raw model output.
4. Compute a lightweight adherence score from simple checklist rules.

## Files

- `experiments/gemini_soundstager/run_soundstager_eval.py`
- `experiments/prompts/soundstager.txt`
- `experiments/results/gemini_soundstager/...` (generated at runtime)

## Run

Create `experiments/gemini_soundstager/.env` from the example:

```bash
cp experiments/gemini_soundstager/.env.example experiments/gemini_soundstager/.env
```

Then edit `.env` and set:

```bash
GEMINI_API_KEY=YOUR_KEY
```

Run one video:

```bash
python3 experiments/gemini_soundstager/run_soundstager_eval.py \
  videos/01_people_crowd__people_sobbing_abab_5s.mp4
```

Run multiple videos:

```bash
python3 experiments/gemini_soundstager/run_soundstager_eval.py videos/*.mp4
```

Use a different model:

```bash
python3 experiments/gemini_soundstager/run_soundstager_eval.py \
  --model gemini-3.1-pro-preview \
  videos/03_golf_driving__dog_howling_abab_5s.mp4
```

Send only the raw target prompt without the added response-format wrapper:

```bash
python3 experiments/gemini_soundstager/run_soundstager_eval.py \
  --raw-prompt-only \
  videos/03_golf_driving__dog_howling_abab_5s.mp4
```

Dry run without calling the API:

```bash
python3 experiments/gemini_soundstager/run_soundstager_eval.py \
  --dry-run \
  videos/03_golf_driving__dog_howling_abab_5s.mp4
```

## Output

Each run creates:

- `prompt_used.txt`: the exact prompt sent to Gemini
- `request_metadata.json`: model, hashes, prompt path, video path, config
- `response_raw.json`: raw Gemini API response
- `response.md`: extracted model text
- `evaluation.json`: simple pass/fail checklist plus score
- `summary.md`: short human-readable summary

The batch root also contains:

- `batch_summary.json`
- `README.md`

## Evaluation Rule

The score is intentionally simple. It checks whether the response includes:

- the five requested top-level sections
- explicit character IDs
- timestamps
- dialogue coverage
- emotional analysis
- narrative analysis

This is a quick prompt-following sanity check, not a full semantic correctness benchmark.

## Notes

- By default the script loads `experiments/gemini_soundstager/.env` before reading `GEMINI_API_KEY`.
- If needed, you can override the path with `--env-file`.
- Shell environment variables still work and take precedence over `.env`.
