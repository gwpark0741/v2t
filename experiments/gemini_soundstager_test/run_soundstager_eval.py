#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "gemini-3.1-pro-preview"
DEFAULT_PROMPT_PATH = Path("experiments/prompts/soundstager.txt")
DEFAULT_OUTPUT_ROOT = Path("experiments/results/gemini_soundstager")
DEFAULT_ENV_FILE = Path("experiments/gemini_soundstager/.env")

REQUIRED_HEADINGS = [
    "Character Tracking",
    "Cut-based Segmentation",
    "Scene-based Segmentation",
    "Semantic-based Segmentation",
    "Additional Analysis",
]

TIMESTAMP_PATTERN = re.compile(r"\b(?:(?:\d{1,2}:)?\d{1,2}:\d{2})\b")
CHARACTER_ID_PATTERN = re.compile(
    r"(?i)\b(?:character|char)\s*(?:id)?\s*[:#-]?\s*[A-Z]?\d+\b|\bid\s*[:#-]\s*[A-Z]?\d+\b"
)
EMOTION_PATTERN = re.compile(r"(?i)\bemotion(?:al)?\b|\bmood\b|\btone\b|\barc\b|\bpeak\b")
DIALOGUE_PATTERN = re.compile(r"(?i)\bdialogue\b|\bspoken\b|\bconversation\b|\bno dialogue\b")
NARRATIVE_PATTERN = re.compile(
    r"(?i)\bnarrative\b|\bstory purpose\b|\bturning point\b|\bsubtext\b|\bdecision\b"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a minimal Gemini video-to-text validation experiment for soundstager."
    )
    parser.add_argument(
        "video_paths",
        nargs="+",
        type=Path,
        help="One or more local video files to analyze.",
    )
    parser.add_argument(
        "--prompt-path",
        type=Path,
        default=DEFAULT_PROMPT_PATH,
        help="Path to the target prompt file.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Gemini model name to call.",
    )
    parser.add_argument(
        "--api-key-env",
        default="GEMINI_API_KEY",
        help="Environment variable that stores the Gemini API key.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="Path to a .env file to load before reading the API key.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for experiment outputs.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="Generation temperature.",
    )
    parser.add_argument(
        "--top-p",
        type=float,
        default=0.95,
        help="Top-p sampling value.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=8192,
        help="Maximum number of output tokens.",
    )
    parser.add_argument(
        "--raw-prompt-only",
        action="store_true",
        help="Send only the target prompt without the lightweight response-format wrapper.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip the API call and only write request metadata.",
    )
    return parser.parse_args()


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "item"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def detect_mime_type(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type:
        return mime_type
    return "application/octet-stream"


def load_prompt(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def load_dotenv(path: Path, *, override: bool = False) -> bool:
    if not path.is_file():
        return False

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        if override or key not in os.environ:
            os.environ[key] = value
    return True


def build_request_prompt(base_prompt: str, *, raw_prompt_only: bool) -> str:
    if raw_prompt_only:
        return base_prompt

    wrapper = """
Analyze the attached video according to the prompt above.

Return a markdown report using these exact top-level headings:
## Character Tracking
## Cut-based Segmentation
## Scene-based Segmentation
## Semantic-based Segmentation
## Additional Analysis

When the clip is too short or evidence is weak, write "Not observable" instead of inventing details.
Keep timestamps as precise as possible.
""".strip()
    return f"{base_prompt}\n\n{wrapper}"


def build_payload(
    *,
    prompt_text: str,
    video_bytes: bytes,
    mime_type: str,
    temperature: float,
    top_p: float,
    max_output_tokens: int,
) -> dict[str, Any]:
    encoded_video = base64.b64encode(video_bytes).decode("ascii")
    return {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt_text},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": encoded_video,
                        }
                    },
                ],
            }
        ],
        "generationConfig": {
            "temperature": temperature,
            "topP": top_p,
            "maxOutputTokens": max_output_tokens,
        },
    }


def call_gemini(*, api_key: str, model: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API request failed ({exc.code}): {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Gemini API request failed: {exc}") from exc


def extract_text(response_json: dict[str, Any]) -> str:
    candidates = response_json.get("candidates") or []
    if not candidates:
        prompt_feedback = response_json.get("promptFeedback")
        if prompt_feedback:
            return json.dumps(prompt_feedback, indent=2)
        return ""

    first_candidate = candidates[0]
    parts = first_candidate.get("content", {}).get("parts", [])
    text_parts = [part["text"] for part in parts if isinstance(part, dict) and "text" in part]
    return "\n\n".join(text_parts).strip()


def normalize_heading_line(line: str) -> str:
    stripped = line.strip()
    stripped = re.sub(r"^#+\s*", "", stripped)
    stripped = re.sub(r"^\d+[\.\)]\s*", "", stripped)
    stripped = stripped.rstrip(":").strip()
    return stripped.casefold()


def has_heading(text: str, heading: str) -> bool:
    target = heading.casefold()
    return any(normalize_heading_line(line) == target for line in text.splitlines())


def build_checks(text: str) -> tuple[list[dict[str, Any]], int]:
    timestamp_count = len(TIMESTAMP_PATTERN.findall(text))
    checks = [
        {
            "name": "has_character_tracking_section",
            "passed": has_heading(text, "Character Tracking"),
            "details": "Response includes the Character Tracking section.",
        },
        {
            "name": "has_cut_segmentation_section",
            "passed": has_heading(text, "Cut-based Segmentation"),
            "details": "Response includes the Cut-based Segmentation section.",
        },
        {
            "name": "has_scene_segmentation_section",
            "passed": has_heading(text, "Scene-based Segmentation"),
            "details": "Response includes the Scene-based Segmentation section.",
        },
        {
            "name": "has_semantic_segmentation_section",
            "passed": has_heading(text, "Semantic-based Segmentation"),
            "details": "Response includes the Semantic-based Segmentation section.",
        },
        {
            "name": "has_additional_analysis_section",
            "passed": has_heading(text, "Additional Analysis"),
            "details": "Response includes the Additional Analysis section.",
        },
        {
            "name": "mentions_character_ids",
            "passed": bool(CHARACTER_ID_PATTERN.search(text)),
            "details": "Response assigns or references explicit character IDs.",
        },
        {
            "name": "uses_timestamps",
            "passed": timestamp_count >= 2,
            "details": f"Response contains at least two timestamps. Found: {timestamp_count}.",
        },
        {
            "name": "mentions_dialogue_ranges",
            "passed": bool(DIALOGUE_PATTERN.search(text)),
            "details": "Response mentions dialogue or the absence of dialogue.",
        },
        {
            "name": "mentions_emotional_analysis",
            "passed": bool(EMOTION_PATTERN.search(text)),
            "details": "Response covers mood, emotional arcs, or emotional peaks.",
        },
        {
            "name": "mentions_narrative_analysis",
            "passed": bool(NARRATIVE_PATTERN.search(text)),
            "details": "Response covers narrative purpose, subtext, or decisions.",
        },
    ]
    return checks, timestamp_count


def evaluate_response(text: str) -> dict[str, Any]:
    checks, timestamp_count = build_checks(text)
    passed = sum(1 for check in checks if check["passed"])
    total = len(checks)
    score = passed / total if total else 0.0
    return {
        "score": score,
        "passed_checks": passed,
        "total_checks": total,
        "timestamp_count": timestamp_count,
        "required_headings": REQUIRED_HEADINGS,
        "checks": checks,
    }


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def build_summary_markdown(
    *,
    model: str,
    prompt_path: Path,
    video_path: Path,
    evaluation: dict[str, Any] | None,
    response_text: str | None,
    error: str | None,
) -> str:
    lines = [
        "# Gemini Soundstager Validation",
        "",
        f"- Model: `{model}`",
        f"- Prompt: `{prompt_path}`",
        f"- Video: `{video_path}`",
    ]

    if error:
        lines.extend(["", "## Status", "", f"- ERROR: {error}"])
        return "\n".join(lines) + "\n"

    if evaluation is None or response_text is None:
        lines.extend(["", "## Status", "", "- Dry run only. No API response generated."])
        return "\n".join(lines) + "\n"

    score = evaluation["score"] * 100
    lines.extend(
        [
            "",
            "## Score",
            "",
            f"- Prompt adherence score: `{score:.1f}%` ({evaluation['passed_checks']}/{evaluation['total_checks']})",
            f"- Timestamp count: `{evaluation['timestamp_count']}`",
            "",
            "## Checks",
            "",
        ]
    )

    for check in evaluation["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        lines.append(f"- [{status}] {check['name']}: {check['details']}")

    preview = response_text.strip()
    if len(preview) > 1600:
        preview = preview[:1600].rstrip() + "\n..."
    lines.extend(["", "## Response Preview", "", preview or "No text returned."])
    return "\n".join(lines) + "\n"


def build_batch_summary_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Gemini Soundstager Batch Summary",
        "",
        "| Video | Status | Score | Output |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        score = row["score"]
        score_cell = f"{score:.1f}%" if score is not None else "-"
        lines.append(
            f"| `{row['video_name']}` | `{row['status']}` | `{score_cell}` | `{row['output_dir']}` |"
        )
    return "\n".join(lines) + "\n"


def relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def main() -> int:
    args = parse_args()
    prompt_text = load_prompt(args.prompt_path)
    request_prompt = build_request_prompt(
        prompt_text, raw_prompt_only=args.raw_prompt_only
    )
    env_file_loaded = load_dotenv(args.env_file)

    batch_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_dir = args.output_root / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    api_key = ""
    if not args.dry_run:
        api_key = os.environ.get(args.api_key_env, "").strip()
        if not api_key:
            raise SystemExit(
                f"Missing API key. Add {args.api_key_env} to {args.env_file} "
                f"or export it in the shell, or rerun with --dry-run."
            )

    batch_rows: list[dict[str, Any]] = []
    had_error = False

    for video_path in args.video_paths:
        if not video_path.is_file():
            raise SystemExit(f"Video file not found: {video_path}")

        video_bytes = video_path.read_bytes()
        mime_type = detect_mime_type(video_path)
        run_dir = batch_dir / slugify(video_path.stem)
        run_dir.mkdir(parents=True, exist_ok=True)

        metadata = {
            "batch_id": batch_id,
            "model": args.model,
            "prompt_path": str(args.prompt_path),
            "env_file": str(args.env_file),
            "env_file_loaded": env_file_loaded,
            "video_path": str(video_path),
            "video_name": video_path.name,
            "video_size_bytes": len(video_bytes),
            "video_sha256": sha256_bytes(video_bytes),
            "mime_type": mime_type,
            "prompt_sha256": sha256_text(prompt_text),
            "request_prompt_sha256": sha256_text(request_prompt),
            "raw_prompt_only": args.raw_prompt_only,
            "temperature": args.temperature,
            "top_p": args.top_p,
            "max_output_tokens": args.max_output_tokens,
            "timestamp": dt.datetime.now().isoformat(),
        }

        write_text(run_dir / "prompt_used.txt", request_prompt + "\n")
        write_json(run_dir / "request_metadata.json", metadata)

        error: str | None = None
        response_text: str | None = None
        evaluation: dict[str, Any] | None = None

        if args.dry_run:
            status = "dry_run"
        else:
            try:
                payload = build_payload(
                    prompt_text=request_prompt,
                    video_bytes=video_bytes,
                    mime_type=mime_type,
                    temperature=args.temperature,
                    top_p=args.top_p,
                    max_output_tokens=args.max_output_tokens,
                )
                response_json = call_gemini(
                    api_key=api_key,
                    model=args.model,
                    payload=payload,
                )
                write_json(run_dir / "response_raw.json", response_json)
                response_text = extract_text(response_json)
                write_text(run_dir / "response.md", response_text + "\n")
                evaluation = evaluate_response(response_text)
                write_json(run_dir / "evaluation.json", evaluation)
                status = "ok"
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                status = "error"
                had_error = True
                write_text(run_dir / "error.txt", error + "\n")

        write_text(
            run_dir / "summary.md",
            build_summary_markdown(
                model=args.model,
                prompt_path=args.prompt_path,
                video_path=video_path,
                evaluation=evaluation,
                response_text=response_text,
                error=error,
            ),
        )

        score_percent = None
        if evaluation is not None:
            score_percent = round(evaluation["score"] * 100, 1)

        batch_rows.append(
            {
                "video_name": video_path.name,
                "status": status,
                "score": score_percent,
                "output_dir": relative_path(run_dir),
            }
        )
        print(
            f"[{status}] {video_path.name} -> {relative_path(run_dir)}"
            + (f" (score={score_percent:.1f}%)" if score_percent is not None else "")
        )

    batch_summary = {
        "batch_id": batch_id,
        "model": args.model,
        "prompt_path": str(args.prompt_path),
        "rows": batch_rows,
    }
    write_json(batch_dir / "batch_summary.json", batch_summary)
    write_text(batch_dir / "README.md", build_batch_summary_markdown(batch_rows))
    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
