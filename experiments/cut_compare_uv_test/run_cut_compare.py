from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import html
import json
import math
import mimetypes
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import imageio_ffmpeg
from scenedetect import SceneManager, open_video
from scenedetect.detectors import AdaptiveDetector


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = REPO_ROOT / "videos"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "outputs" / "cut_compare_uv_test"
DEFAULT_PROMPT_PATH = (
    REPO_ROOT
    / "experiments"
    / "cut_compare_uv_test"
    / "prompts"
    / "gemini_agent_a_cut_compare_prompt.txt"
)
DEFAULT_ENV_FILE = REPO_ROOT / "experiments" / "gemini_soundstager_test" / ".env"
DEFAULT_GEMINI_MAX_OUTPUT_TOKENS = 16384


@dataclass
class VideoMetadata:
    fps: float
    frame_count: int
    duration_seconds: float
    width: int
    height: int

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass
class CutSegment:
    id: str
    start_seconds: float
    end_seconds: float
    start_timecode: str
    end_timecode: str
    duration_seconds: float
    confidence: float | None = None
    notes: str | None = None
    transition_in: str | None = None
    transition_out: str | None = None
    camera_angle: str | None = None

    def boundary_seconds(self) -> float:
        return self.end_seconds


@dataclass
class MethodResult:
    method: str
    video_name: str
    video_path: str
    output_dir: str
    parameters: dict[str, Any]
    metadata: dict[str, Any]
    cuts: list[CutSegment]
    clips: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_response_path: str | None = None
    response_text_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["cuts"] = [asdict(cut) for cut in self.cuts]
        return payload


@dataclass
class ComparisonResult:
    video_name: str
    video_path: str
    tolerance_seconds: float
    pyscenedetect_cut_count: int
    gemini_cut_count: int
    pyscenedetect_boundary_count: int
    gemini_boundary_count: int
    matched_boundary_count: int
    precision_vs_pyscenedetect: float
    recall_vs_pyscenedetect: float
    f1_score: float
    exact_match: bool
    differing: bool
    matched_boundaries: list[dict[str, Any]]
    unmatched_pyscenedetect_boundaries: list[float]
    unmatched_gemini_boundaries: list[float]
    gemini_warning_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PairwiseVideoResult:
    video_slug: str
    comparison: ComparisonResult
    pyscenedetect_result: MethodResult
    gemini_result: MethodResult


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare PySceneDetect cuts with Gemini cut segmentation output."
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--video", type=Path, help="Analyze a single video.")
    source_group.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing input videos.",
    )
    parser.add_argument("--glob", default="*.mp4", help="Video glob for --input-dir mode.")
    parser.add_argument("--limit", type=int, default=None, help="Optional video count cap.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for run outputs.",
    )
    parser.add_argument(
        "--prompt-path",
        type=Path,
        default=DEFAULT_PROMPT_PATH,
        help="Prompt template for Gemini cut extraction.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="Optional .env file with GEMINI_API_KEY.",
    )
    parser.add_argument(
        "--gemini-model",
        default="gemini-2.5-pro",
        help="Gemini model name for cut extraction.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=3.5,
        help="PySceneDetect AdaptiveDetector adaptive_threshold.",
    )
    parser.add_argument(
        "--min-scene-len",
        type=int,
        default=30,
        help="PySceneDetect minimum scene length in frames.",
    )
    parser.add_argument(
        "--window-width",
        type=int,
        default=2,
        help="PySceneDetect AdaptiveDetector window width.",
    )
    parser.add_argument(
        "--min-content-val",
        type=float,
        default=15.0,
        help="PySceneDetect AdaptiveDetector minimum content value.",
    )
    parser.add_argument(
        "--boundary-tolerance",
        type=float,
        default=0.5,
        help="Boundary matching tolerance in seconds.",
    )
    parser.add_argument(
        "--skip-gemini",
        action="store_true",
        help="Skip Gemini and run only PySceneDetect.",
    )
    parser.add_argument(
        "--skip-clips",
        action="store_true",
        help="Skip clip generation.",
    )
    parser.add_argument(
        "--dry-run-gemini",
        action="store_true",
        help="Write Gemini request artifacts without calling the API.",
    )
    parser.add_argument(
        "--report-run-dir",
        type=Path,
        help="Rebuild aggregate HTML reports from an existing run directory.",
    )
    return parser.parse_args()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def persist_method_result(output_dir: Path, result: MethodResult) -> None:
    write_json(output_dir / "result.json", result.to_dict())


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "item"


def format_seconds(seconds: float) -> str:
    total_millis = int(round(seconds * 1000))
    hours, rem = divmod(total_millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
    return f"{minutes:02d}:{secs:02d}.{millis:03d}"


def snap_to_grid(seconds: float, grid: float = 0.2) -> float:
    return round(round(seconds / grid) * grid, 3)


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def relative_path(from_dir: Path, to_path: Path) -> str:
    return os.path.relpath(to_path, start=from_dir)


def file_time_label(seconds: float) -> str:
    return format_seconds(seconds).replace(":", "-").replace(".", "_")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


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


def detect_mime_type(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type:
        return mime_type
    return "application/octet-stream"


def resolve_videos(args: argparse.Namespace) -> list[Path]:
    if args.video is not None:
        return [args.video.resolve()]

    input_dir = args.input_dir.resolve()
    if not input_dir.exists():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    videos = sorted(path.resolve() for path in input_dir.glob(args.glob) if path.is_file())
    if args.limit is not None:
        videos = videos[: args.limit]
    return videos


def read_video_metadata(video_path: Path) -> VideoMetadata:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Failed to open video: {video_path}")

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    capture.release()

    if fps <= 0 or frame_count <= 0:
        raise RuntimeError(f"Invalid video metadata for: {video_path}")

    return VideoMetadata(
        fps=fps,
        frame_count=frame_count,
        duration_seconds=frame_count / fps,
        width=width,
        height=height,
    )


def build_cut_segments_from_boundaries(
    *,
    boundaries: list[float],
    duration_seconds: float,
    prefix: str,
    transition_in: str | None = None,
    transition_out: str | None = None,
    confidence: float | None = None,
    notes: str | None = None,
) -> list[CutSegment]:
    clean_boundaries = sorted(
        {
            round(boundary, 3)
            for boundary in boundaries
            if 0.0 < boundary < duration_seconds
        }
    )
    segment_edges = [0.0, *clean_boundaries, duration_seconds]
    cuts: list[CutSegment] = []

    for index, (start_seconds, end_seconds) in enumerate(
        zip(segment_edges, segment_edges[1:]), start=1
    ):
        start_seconds = round(start_seconds, 3)
        end_seconds = round(end_seconds, 3)
        if end_seconds <= start_seconds:
            continue
        cuts.append(
            CutSegment(
                id=f"{prefix}_{index:03d}",
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                start_timecode=format_seconds(start_seconds),
                end_timecode=format_seconds(end_seconds),
                duration_seconds=round(end_seconds - start_seconds, 3),
                confidence=confidence,
                notes=notes,
                transition_in=transition_in,
                transition_out=transition_out,
            )
        )
    return cuts


def run_pyscenedetect(
    *,
    video_path: Path,
    metadata: VideoMetadata,
    output_dir: Path,
    threshold: float,
    min_scene_len: int,
    window_width: int,
    min_content_val: float,
) -> MethodResult:
    video = open_video(str(video_path))
    scene_manager = SceneManager()
    scene_manager.add_detector(
        AdaptiveDetector(
            adaptive_threshold=threshold,
            min_scene_len=min_scene_len,
            window_width=window_width,
            min_content_val=min_content_val,
        )
    )
    scene_manager.detect_scenes(video=video, show_progress=True)

    scene_list = scene_manager.get_scene_list(start_in_scene=True)
    boundaries = [start.get_seconds() for start, _ in scene_list[1:]]
    cuts = build_cut_segments_from_boundaries(
        boundaries=boundaries,
        duration_seconds=metadata.duration_seconds,
        prefix="PSCENE_CUT",
    )

    result = MethodResult(
        method="pyscenedetect",
        video_name=video_path.name,
        video_path=str(video_path),
        output_dir=str(output_dir),
        parameters={
            "detector": "AdaptiveDetector",
            "adaptive_threshold": threshold,
            "min_scene_len_frames": min_scene_len,
            "window_width": window_width,
            "min_content_val": min_content_val,
        },
        metadata={
            "detector": "AdaptiveDetector",
            "scene_count": len(scene_list),
            "boundary_count": len(boundaries),
            "duration_seconds": round(metadata.duration_seconds, 3),
            "fps": metadata.fps,
            "frame_count": metadata.frame_count,
            "resolution": metadata.resolution,
        },
        cuts=cuts,
    )
    persist_method_result(output_dir, result)
    return result


def load_prompt_template(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def build_gemini_prompt(
    *,
    template_text: str,
    scene_change_candidates: list[float],
    metadata: VideoMetadata,
) -> str:
    prompt = template_text
    prompt = prompt.replace(
        "__DURATION_SECONDS__", str(round(metadata.duration_seconds, 3))
    )
    prompt = prompt.replace("__FPS__", "2")
    prompt = prompt.replace("__RESOLUTION__", metadata.resolution)
    prompt = prompt.replace(
        "__SCENE_CHANGE_CANDIDATES__",
        json.dumps([round(value, 3) for value in scene_change_candidates]),
    )
    return prompt


def build_gemini_payload(
    *,
    prompt_text: str,
    video_bytes: bytes,
    mime_type: str,
    max_output_tokens: int = DEFAULT_GEMINI_MAX_OUTPUT_TOKENS,
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
            "temperature": 0.1,
            "topP": 0.95,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
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


def extract_response_text(response_json: dict[str, Any]) -> str:
    candidates = response_json.get("candidates") or []
    if not candidates:
        prompt_feedback = response_json.get("promptFeedback")
        if prompt_feedback:
            return json.dumps(prompt_feedback, indent=2, ensure_ascii=False)
        return ""

    parts = candidates[0].get("content", {}).get("parts", [])
    text_parts = [part["text"] for part in parts if isinstance(part, dict) and "text" in part]
    return "\n\n".join(text_parts).strip()


def first_finish_reason(response_json: dict[str, Any]) -> str | None:
    candidates = response_json.get("candidates") or []
    if not candidates:
        return None
    finish_reason = candidates[0].get("finishReason")
    return str(finish_reason) if finish_reason is not None else None


def extract_json_object_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Empty Gemini response text.")

    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped

    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, flags=re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()

    in_string = False
    escaped = False
    depth = 0
    start_index: int | None = None

    for index, char in enumerate(stripped):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start_index = index
            depth += 1
            continue
        if char == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start_index is not None:
                return stripped[start_index : index + 1]

    raise ValueError("Could not extract JSON object from Gemini response.")


def parse_json_payload(text: str) -> dict[str, Any]:
    return json.loads(extract_json_object_text(text))


def build_compact_retry_prompt(base_prompt: str) -> str:
    compact_schema = """
The previous response was too long or not valid JSON.

Repeat the same cut analysis, but return only this compact JSON schema:
{
  "video_duration": number,
  "cuts": [
    {
      "id": "CUT_001",
      "start_time": number,
      "end_time": number,
      "camera_angle": "string",
      "transition_in": "string",
      "transition_out": "string",
      "camera_notes": "short string",
      "confidence": number
    }
  ]
}

Do not include characters, objects, backgrounds, or ambience_sources.
Keep camera_notes short.
Return JSON only.
""".strip()
    return f"{base_prompt}\n\n{compact_schema}"


def parse_time_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 3)
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return round(float(text), 3)

    match = re.fullmatch(r"(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return round(hours * 3600 + minutes * 60 + seconds, 3)


def normalize_gemini_cuts(
    *,
    raw_payload: dict[str, Any],
    duration_seconds: float,
) -> tuple[list[CutSegment], list[str]]:
    raw_cuts = raw_payload.get("cuts")
    if not isinstance(raw_cuts, list):
        raise ValueError("Gemini JSON did not contain a cuts[] list.")

    prepared: list[dict[str, Any]] = []
    for index, raw_cut in enumerate(raw_cuts, start=1):
        if not isinstance(raw_cut, dict):
            continue
        prepared.append(
            {
                "raw_index": index,
                "id": str(raw_cut.get("id") or f"GEMINI_CUT_{index:03d}"),
                "start_time": parse_time_value(raw_cut.get("start_time")),
                "end_time": parse_time_value(raw_cut.get("end_time")),
                "camera_angle": raw_cut.get("camera_angle"),
                "transition_in": raw_cut.get("transition_in"),
                "transition_out": raw_cut.get("transition_out"),
                "camera_notes": raw_cut.get("camera_notes"),
                "confidence": raw_cut.get("confidence"),
            }
        )

    if not prepared:
        raise ValueError("Gemini returned no usable cuts.")

    prepared.sort(
        key=lambda item: (
            item["start_time"] if item["start_time"] is not None else math.inf,
            item["end_time"] if item["end_time"] is not None else math.inf,
            item["raw_index"],
        )
    )

    warnings: list[str] = []
    cuts: list[CutSegment] = []
    last_end = 0.0

    for index, item in enumerate(prepared, start=1):
        start_time = item["start_time"]
        end_time = item["end_time"]

        if index == 1 and (start_time is None or start_time > 0.0):
            warnings.append(
                f"Adjusted first Gemini cut start to 0.0 from {start_time!r}."
            )
            start_time = 0.0

        if start_time is None:
            start_time = last_end
            warnings.append(
                f"Filled missing start_time for Gemini cut {item['id']} with {last_end:.3f}."
            )

        start_time = snap_to_grid(max(0.0, min(start_time, duration_seconds)))
        if start_time < last_end:
            warnings.append(
                f"Clamped overlapping Gemini cut {item['id']} start from {start_time:.3f} to {last_end:.3f}."
            )
            start_time = last_end
        elif start_time > last_end:
            warnings.append(
                f"Closed gap before Gemini cut {item['id']} by shifting start from {start_time:.3f} to {last_end:.3f}."
            )
            start_time = last_end

        if end_time is None:
            if index < len(prepared):
                next_start = prepared[index].get("start_time")
                end_time = next_start if next_start is not None else duration_seconds
            else:
                end_time = duration_seconds
            warnings.append(
                f"Filled missing end_time for Gemini cut {item['id']} with {end_time:.3f}."
            )

        end_time = snap_to_grid(max(0.0, min(end_time, duration_seconds)))
        if index == len(prepared) and end_time != snap_to_grid(duration_seconds):
            warnings.append(
                f"Extended last Gemini cut {item['id']} end from {end_time:.3f} to {duration_seconds:.3f}."
            )
            end_time = snap_to_grid(duration_seconds)

        if end_time <= start_time:
            if index == len(prepared) and duration_seconds > start_time:
                end_time = snap_to_grid(duration_seconds)
                warnings.append(
                    f"Adjusted zero-length final Gemini cut {item['id']} to video end."
                )
            else:
                warnings.append(
                    f"Dropped Gemini cut {item['id']} because end_time <= start_time."
                )
                continue

        cut = CutSegment(
            id=str(item["id"]),
            start_seconds=round(start_time, 3),
            end_seconds=round(end_time, 3),
            start_timecode=format_seconds(start_time),
            end_timecode=format_seconds(end_time),
            duration_seconds=round(end_time - start_time, 3),
            confidence=(
                float(item["confidence"])
                if isinstance(item["confidence"], (int, float))
                else None
            ),
            notes=item["camera_notes"],
            transition_in=item["transition_in"],
            transition_out=item["transition_out"],
            camera_angle=item["camera_angle"],
        )
        cuts.append(cut)
        last_end = cut.end_seconds

    if not cuts:
        raise ValueError("Gemini cuts were all dropped during normalization.")

    if cuts[0].start_seconds != 0.0:
        warnings.append("Forced first Gemini cut to start at 0.0.")
        cuts[0].start_seconds = 0.0
        cuts[0].start_timecode = format_seconds(0.0)
        cuts[0].duration_seconds = round(cuts[0].end_seconds - cuts[0].start_seconds, 3)

    final_duration = snap_to_grid(duration_seconds)
    if cuts[-1].end_seconds != final_duration:
        warnings.append(
            f"Forced last Gemini cut to end at video duration {final_duration:.3f}."
        )
        cuts[-1].end_seconds = final_duration
        cuts[-1].end_timecode = format_seconds(final_duration)
        cuts[-1].duration_seconds = round(cuts[-1].end_seconds - cuts[-1].start_seconds, 3)

    for index, cut in enumerate(cuts, start=1):
        cut.id = cut.id or f"GEMINI_CUT_{index:03d}"

    return cuts, warnings


def run_gemini_cut_extraction(
    *,
    video_path: Path,
    metadata: VideoMetadata,
    scene_change_candidates: list[float],
    output_dir: Path,
    prompt_path: Path,
    model: str,
    env_file: Path,
    dry_run: bool,
) -> MethodResult | None:
    prompt_template = load_prompt_template(prompt_path)
    request_prompt = build_gemini_prompt(
        template_text=prompt_template,
        scene_change_candidates=scene_change_candidates,
        metadata=metadata,
    )
    video_bytes = video_path.read_bytes()
    mime_type = detect_mime_type(video_path)
    env_loaded = load_dotenv(env_file)
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    write_text(output_dir / "prompt_used.txt", request_prompt + "\n")
    request_metadata = {
        "model": model,
        "prompt_path": str(prompt_path),
        "env_file": str(env_file),
        "env_file_loaded": env_loaded,
        "video_name": video_path.name,
        "video_path": str(video_path),
        "video_size_bytes": len(video_bytes),
        "video_sha256": sha256_bytes(video_bytes),
        "mime_type": mime_type,
        "prompt_sha256": sha256_text(prompt_template),
        "request_prompt_sha256": sha256_text(request_prompt),
        "duration_seconds": round(metadata.duration_seconds, 3),
        "native_fps": round(metadata.fps, 3),
        "analysis_fps": 2,
        "scene_change_candidates": scene_change_candidates,
        "timestamp": dt.datetime.now().isoformat(),
    }
    write_json(output_dir / "request_metadata.json", request_metadata)

    if dry_run:
        return None
    if not api_key:
        raise SystemExit(
            f"Missing GEMINI_API_KEY. Set it in {env_file} or export it in the shell."
        )

    payload = build_gemini_payload(
        prompt_text=request_prompt,
        video_bytes=video_bytes,
        mime_type=mime_type,
        max_output_tokens=DEFAULT_GEMINI_MAX_OUTPUT_TOKENS,
    )
    response_json = call_gemini(api_key=api_key, model=model, payload=payload)
    write_json(output_dir / "response_raw.json", response_json)
    response_text = extract_response_text(response_json)
    write_text(output_dir / "response.md", response_text + "\n")
    warnings: list[str] = []
    raw_response_path = output_dir / "response_raw.json"
    response_text_path = output_dir / "response.md"

    try:
        parsed_payload = parse_json_payload(response_text)
    except Exception as exc:  # noqa: BLE001
        finish_reason = first_finish_reason(response_json)
        warnings.append(
            "Primary Gemini response could not be parsed as JSON"
            + (f" (finish_reason={finish_reason})" if finish_reason else "")
            + "."
        )
        retry_prompt = build_compact_retry_prompt(request_prompt)
        write_text(output_dir / "prompt_retry_used.txt", retry_prompt + "\n")
        retry_payload = build_gemini_payload(
            prompt_text=retry_prompt,
            video_bytes=video_bytes,
            mime_type=mime_type,
            max_output_tokens=4096,
        )
        retry_response_json = call_gemini(api_key=api_key, model=model, payload=retry_payload)
        write_json(output_dir / "response_retry_raw.json", retry_response_json)
        retry_response_text = extract_response_text(retry_response_json)
        write_text(output_dir / "response_retry.md", retry_response_text + "\n")
        try:
            parsed_payload = parse_json_payload(retry_response_text)
        except Exception as retry_exc:  # noqa: BLE001
            raise RuntimeError(
                f"Failed to parse Gemini JSON after retry. "
                f"Primary error: {exc}. Retry error: {retry_exc}."
            ) from retry_exc
        warnings.append("Used compact cuts-only retry response after primary parse failure.")
        raw_response_path = output_dir / "response_retry_raw.json"
        response_text_path = output_dir / "response_retry.md"

    cuts, normalize_warnings = normalize_gemini_cuts(
        raw_payload=parsed_payload,
        duration_seconds=metadata.duration_seconds,
    )
    warnings.extend(normalize_warnings)
    result = MethodResult(
        method="gemini",
        video_name=video_path.name,
        video_path=str(video_path),
        output_dir=str(output_dir),
        parameters={
            "model": model,
            "analysis_fps": 2,
            "prompt_path": str(prompt_path),
        },
        metadata={
            "duration_seconds": round(metadata.duration_seconds, 3),
            "fps": metadata.fps,
            "frame_count": metadata.frame_count,
            "resolution": metadata.resolution,
            "scene_change_candidate_count": len(scene_change_candidates),
            "gemini_top_level_keys": sorted(parsed_payload.keys()),
        },
        cuts=cuts,
        warnings=warnings,
        raw_response_path=str(raw_response_path),
        response_text_path=str(response_text_path),
    )
    write_json(output_dir / "parsed_response.json", parsed_payload)
    persist_method_result(output_dir, result)
    return result


def cut_boundaries(cuts: list[CutSegment]) -> list[float]:
    if len(cuts) <= 1:
        return []
    return [round(cut.end_seconds, 3) for cut in cuts[:-1]]


def compare_method_results(
    *,
    video_path: Path,
    pyscenedetect_result: MethodResult,
    gemini_result: MethodResult,
    tolerance_seconds: float,
) -> ComparisonResult:
    pyscene_boundaries = cut_boundaries(pyscenedetect_result.cuts)
    gemini_boundaries = cut_boundaries(gemini_result.cuts)

    matched_boundaries: list[dict[str, Any]] = []
    unmatched_pyscene: list[float] = []
    unmatched_gemini: list[float] = []

    left_index = 0
    right_index = 0
    while left_index < len(pyscene_boundaries) and right_index < len(gemini_boundaries):
        pyscene_time = pyscene_boundaries[left_index]
        gemini_time = gemini_boundaries[right_index]
        delta = round(gemini_time - pyscene_time, 3)

        if abs(delta) <= tolerance_seconds:
            matched_boundaries.append(
                {
                    "pyscenedetect": pyscene_time,
                    "gemini": gemini_time,
                    "delta_seconds": delta,
                }
            )
            left_index += 1
            right_index += 1
        elif pyscene_time < gemini_time:
            unmatched_pyscene.append(pyscene_time)
            left_index += 1
        else:
            unmatched_gemini.append(gemini_time)
            right_index += 1

    unmatched_pyscene.extend(pyscene_boundaries[left_index:])
    unmatched_gemini.extend(gemini_boundaries[right_index:])

    matched_count = len(matched_boundaries)
    precision = (
        matched_count / len(gemini_boundaries) if gemini_boundaries else 1.0
    )
    recall = (
        matched_count / len(pyscene_boundaries) if pyscene_boundaries else 1.0
    )
    f1_score = (
        0.0
        if precision + recall == 0.0
        else (2 * precision * recall) / (precision + recall)
    )
    exact_match = not unmatched_pyscene and not unmatched_gemini

    return ComparisonResult(
        video_name=video_path.name,
        video_path=str(video_path),
        tolerance_seconds=tolerance_seconds,
        pyscenedetect_cut_count=len(pyscenedetect_result.cuts),
        gemini_cut_count=len(gemini_result.cuts),
        pyscenedetect_boundary_count=len(pyscene_boundaries),
        gemini_boundary_count=len(gemini_boundaries),
        matched_boundary_count=matched_count,
        precision_vs_pyscenedetect=round(precision, 4),
        recall_vs_pyscenedetect=round(recall, 4),
        f1_score=round(f1_score, 4),
        exact_match=exact_match,
        differing=not exact_match,
        matched_boundaries=matched_boundaries,
        unmatched_pyscenedetect_boundaries=unmatched_pyscene,
        unmatched_gemini_boundaries=unmatched_gemini,
        gemini_warning_count=len(gemini_result.warnings),
    )


def render_clip(
    *,
    video_path: Path,
    output_path: Path,
    start_seconds: float,
    end_seconds: float,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration_seconds = round(end_seconds - start_seconds, 3)
    if duration_seconds <= 0:
        raise ValueError("Clip duration must be positive.")

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    codec_variants = [
        [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
        ],
        [
            "-c:v",
            "mpeg4",
            "-q:v",
            "2",
            "-movflags",
            "+faststart",
        ],
    ]
    last_error = "unknown ffmpeg error"

    for codec_args in codec_variants:
        command = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-i",
            str(video_path),
            "-t",
            f"{duration_seconds:.3f}",
            "-an",
            *codec_args,
            str(output_path),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            return
        last_error = completed.stderr.strip() or completed.stdout.strip() or last_error

    raise RuntimeError(f"Failed to render clip {output_path.name}: {last_error}")


def generate_method_clips(
    *,
    video_path: Path,
    method_result: MethodResult,
    output_dir: Path,
) -> None:
    clips_dir = output_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    rendered_clips: list[dict[str, Any]] = []
    for index, cut in enumerate(method_result.cuts, start=1):
        clip_name = (
            f"clip_{index:03d}__"
            f"{file_time_label(cut.start_seconds)}__"
            f"{file_time_label(cut.end_seconds)}.mp4"
        )
        clip_path = clips_dir / clip_name
        render_clip(
            video_path=video_path,
            output_path=clip_path,
            start_seconds=cut.start_seconds,
            end_seconds=cut.end_seconds,
        )
        rendered_clips.append(
            {
                "clip_index": index,
                "cut_id": cut.id,
                "start_seconds": cut.start_seconds,
                "end_seconds": cut.end_seconds,
                "start_timecode": cut.start_timecode,
                "end_timecode": cut.end_timecode,
                "duration_seconds": cut.duration_seconds,
                "file_path": str(clip_path),
            }
        )

    method_result.clips = rendered_clips
    write_json(output_dir / "clips.json", {"method": method_result.method, "clips": rendered_clips})
    persist_method_result(output_dir, method_result)


def build_cut_rows(cuts: list[CutSegment]) -> str:
    rows = []
    for cut in cuts:
        rows.append(
            "<tr>"
            f"<td>{html.escape(cut.id)}</td>"
            f"<td>{cut.start_timecode}</td>"
            f"<td>{cut.end_timecode}</td>"
            f"<td>{cut.duration_seconds:.3f}</td>"
            f"<td>{html.escape(cut.camera_angle or '')}</td>"
            f"<td>{html.escape(cut.transition_in or '')}</td>"
            f"<td>{html.escape(cut.transition_out or '')}</td>"
            f"<td>{html.escape(cut.notes or '')}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_clip_cards(*, page_dir: Path, clips: list[dict[str, Any]]) -> str:
    cards = []
    for clip in clips:
        clip_path = Path(clip["file_path"])
        rel = relative_path(page_dir, clip_path)
        title = (
            f"{clip['cut_id']} "
            f"({clip['start_timecode']} - {clip['end_timecode']})"
        )
        cards.append(
            "<div class='clip-card'>"
            f"<h4>{html.escape(title)}</h4>"
            f"<video controls preload='metadata' src='{html.escape(rel)}'></video>"
            "</div>"
        )
    return "\n".join(cards) if cards else "<p>No clips generated.</p>"


def build_match_rows(comparison: ComparisonResult) -> str:
    rows = []
    for item in comparison.matched_boundaries:
        rows.append(
            "<tr>"
            f"<td>{item['pyscenedetect']:.3f}</td>"
            f"<td>{item['gemini']:.3f}</td>"
            f"<td>{item['delta_seconds']:.3f}</td>"
            "</tr>"
        )
    if not rows:
        return "<tr><td colspan='3'>No matched boundaries.</td></tr>"
    return "\n".join(rows)


def build_value_list(values: list[float]) -> str:
    if not values:
        return "None"
    return ", ".join(f"{value:.3f}s" for value in values)


def method_result_from_dict(payload: dict[str, Any]) -> MethodResult:
    return MethodResult(
        method=payload["method"],
        video_name=payload["video_name"],
        video_path=payload["video_path"],
        output_dir=payload["output_dir"],
        parameters=payload.get("parameters", {}),
        metadata=payload.get("metadata", {}),
        cuts=[CutSegment(**cut) for cut in payload.get("cuts", [])],
        clips=payload.get("clips", []),
        warnings=payload.get("warnings", []),
        raw_response_path=payload.get("raw_response_path"),
        response_text_path=payload.get("response_text_path"),
    )


def comparison_result_from_dict(payload: dict[str, Any]) -> ComparisonResult:
    return ComparisonResult(
        video_name=payload["video_name"],
        video_path=payload["video_path"],
        tolerance_seconds=payload["tolerance_seconds"],
        pyscenedetect_cut_count=payload["pyscenedetect_cut_count"],
        gemini_cut_count=payload["gemini_cut_count"],
        pyscenedetect_boundary_count=payload["pyscenedetect_boundary_count"],
        gemini_boundary_count=payload["gemini_boundary_count"],
        matched_boundary_count=payload["matched_boundary_count"],
        precision_vs_pyscenedetect=payload["precision_vs_pyscenedetect"],
        recall_vs_pyscenedetect=payload["recall_vs_pyscenedetect"],
        f1_score=payload["f1_score"],
        exact_match=payload["exact_match"],
        differing=payload["differing"],
        matched_boundaries=payload.get("matched_boundaries", []),
        unmatched_pyscenedetect_boundaries=payload.get(
            "unmatched_pyscenedetect_boundaries", []
        ),
        unmatched_gemini_boundaries=payload.get("unmatched_gemini_boundaries", []),
        gemini_warning_count=payload.get("gemini_warning_count", 0),
    )


def load_pairwise_video_results(run_dir: Path) -> tuple[list[PairwiseVideoResult], list[dict[str, Any]], float]:
    summary_payload = load_json(run_dir / "comparison_summary.json")
    comparison_rows = summary_payload.get("videos", [])
    boundary_tolerance = float(summary_payload.get("boundary_tolerance_seconds", 0.5))
    results: list[PairwiseVideoResult] = []

    for row in comparison_rows:
        comparison = comparison_result_from_dict(row)
        video_slug = slugify(Path(comparison.video_name).stem)
        video_dir = run_dir / video_slug
        pyscenedetect_result = method_result_from_dict(
            load_json(video_dir / "pyscenedetect" / "result.json")
        )
        gemini_result = method_result_from_dict(
            load_json(video_dir / "gemini" / "result.json")
        )
        results.append(
            PairwiseVideoResult(
                video_slug=video_slug,
                comparison=comparison,
                pyscenedetect_result=pyscenedetect_result,
                gemini_result=gemini_result,
            )
        )

    return results, comparison_rows, boundary_tolerance


def build_video_report_html(
    *,
    page_path: Path,
    video_name: str,
    pyscenedetect_result: MethodResult,
    gemini_result: MethodResult,
    comparison: ComparisonResult,
) -> str:
    page_dir = page_path.parent
    pyscene_cards = build_clip_cards(page_dir=page_dir, clips=pyscenedetect_result.clips)
    gemini_cards = build_clip_cards(page_dir=page_dir, clips=gemini_result.clips)
    title = f"Cut Comparison - {video_name}"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #f6f1e8;
      --panel: #fffaf2;
      --ink: #1d1a17;
      --muted: #6e645b;
      --accent: #0f766e;
      --accent-soft: #d7efe9;
      --border: #dccfc1;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Georgia, "Iowan Old Style", serif; background: linear-gradient(180deg, #f8f3eb 0%, #efe4d2 100%); color: var(--ink); }}
    main {{ max-width: 1400px; margin: 0 auto; padding: 32px 20px 80px; }}
    h1, h2, h3, h4 {{ margin: 0 0 12px; }}
    p, li, td, th {{ line-height: 1.45; }}
    .panel {{ background: rgba(255, 250, 242, 0.92); border: 1px solid var(--border); border-radius: 18px; padding: 18px; margin-bottom: 20px; box-shadow: 0 10px 30px rgba(70, 54, 36, 0.08); }}
    .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; }}
    .summary-card {{ padding: 14px; border-radius: 14px; background: var(--accent-soft); border: 1px solid #b8ddd5; }}
    .summary-card strong {{ display: block; font-size: 1.4rem; margin-bottom: 6px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }}
    th {{ background: rgba(215, 239, 233, 0.55); }}
    .clip-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }}
    .clip-card {{ background: white; border: 1px solid var(--border); border-radius: 14px; padding: 12px; }}
    .clip-card video {{ width: 100%; border-radius: 10px; background: #000; }}
    .muted {{ color: var(--muted); }}
    .warning-list {{ margin: 10px 0 0; padding-left: 18px; color: #8a3b12; }}
    .badge {{ display: inline-block; padding: 4px 8px; border-radius: 999px; background: #1f2937; color: #fff; font-size: 0.9rem; }}
    .badge.diff {{ background: #b91c1c; }}
    .badge.match {{ background: #15803d; }}
    a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <main>
    <div class="panel">
      <p><a href="../comparison_report.html">Back to full report</a> | <a href="../pairwise_comparison_report.html">Pairwise full report</a> | <a href="../pairwise_differing_report.html">Pairwise differing only</a> | <a href="../differing_videos_report.html">Differing videos only</a></p>
      <h1>{html.escape(video_name)}</h1>
      <p class="muted">Tolerance: {comparison.tolerance_seconds:.1f}s</p>
      <p><span class="badge {'diff' if comparison.differing else 'match'}">{'DIFFERENT' if comparison.differing else 'MATCH'}</span></p>
    </div>

    <div class="panel">
      <h2>Comparison Summary</h2>
      <div class="summary-grid">
        <div class="summary-card"><strong>{comparison.pyscenedetect_cut_count}</strong>PySceneDetect cuts</div>
        <div class="summary-card"><strong>{comparison.gemini_cut_count}</strong>Gemini cuts</div>
        <div class="summary-card"><strong>{comparison.matched_boundary_count}</strong>Matched boundaries</div>
        <div class="summary-card"><strong>{comparison.f1_score:.3f}</strong>Boundary F1</div>
      </div>
      <p><strong>Unmatched PySceneDetect boundaries:</strong> {html.escape(build_value_list(comparison.unmatched_pyscenedetect_boundaries))}</p>
      <p><strong>Unmatched Gemini boundaries:</strong> {html.escape(build_value_list(comparison.unmatched_gemini_boundaries))}</p>
    </div>

    <div class="panel">
      <h2>Boundary Matches</h2>
      <table>
        <thead>
          <tr>
            <th>PySceneDetect</th>
            <th>Gemini</th>
            <th>Delta (Gemini - PySceneDetect)</th>
          </tr>
        </thead>
        <tbody>
          {build_match_rows(comparison)}
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>PySceneDetect Clips</h2>
      <p class="muted">AdaptiveDetector | adaptive_threshold={pyscenedetect_result.parameters['adaptive_threshold']}, min_scene_len={pyscenedetect_result.parameters['min_scene_len_frames']} frames, window_width={pyscenedetect_result.parameters['window_width']}, min_content_val={pyscenedetect_result.parameters['min_content_val']}</p>
      <div class="clip-grid">
        {pyscene_cards}
      </div>
      <h3>PySceneDetect Cuts</h3>
      <table>
        <thead>
          <tr>
            <th>Cut ID</th>
            <th>Start</th>
            <th>End</th>
            <th>Duration</th>
            <th>Camera Angle</th>
            <th>Transition In</th>
            <th>Transition Out</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {build_cut_rows(pyscenedetect_result.cuts)}
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>Gemini Clips</h2>
      <p class="muted">Model={html.escape(str(gemini_result.parameters['model']))}, analysis_fps=2</p>
      <div class="clip-grid">
        {gemini_cards}
      </div>
      <h3>Gemini Cuts</h3>
      <table>
        <thead>
          <tr>
            <th>Cut ID</th>
            <th>Start</th>
            <th>End</th>
            <th>Duration</th>
            <th>Camera Angle</th>
            <th>Transition In</th>
            <th>Transition Out</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {build_cut_rows(gemini_result.cuts)}
        </tbody>
      </table>
      {"<ul class='warning-list'>" + "".join(f"<li>{html.escape(warning)}</li>" for warning in gemini_result.warnings) + "</ul>" if gemini_result.warnings else "<p class='muted'>No Gemini normalization warnings.</p>"}
    </div>
  </main>
</body>
</html>
"""


def build_overview_rows(*, run_dir: Path, rows: list[dict[str, Any]]) -> str:
    rendered_rows = []
    for row in rows:
        video_slug = slugify(Path(row["video_name"]).stem)
        report_rel = relative_path(run_dir, run_dir / video_slug / "report.html")
        status_text = "DIFFERENT" if row["differing"] else "MATCH"
        rendered_rows.append(
            "<tr>"
            f"<td>{html.escape(row['video_name'])}</td>"
            f"<td>{row['pyscenedetect_cut_count']}</td>"
            f"<td>{row['gemini_cut_count']}</td>"
            f"<td>{row['matched_boundary_count']}</td>"
            f"<td>{row['precision_vs_pyscenedetect']:.3f}</td>"
            f"<td>{row['recall_vs_pyscenedetect']:.3f}</td>"
            f"<td>{row['f1_score']:.3f}</td>"
            f"<td>{status_text}</td>"
            f"<td><a href='{html.escape(report_rel)}'>open report</a></td>"
            "</tr>"
        )
    if not rendered_rows:
        return "<tr><td colspan='9'>No compared videos.</td></tr>"
    return "\n".join(rendered_rows)


def build_batch_report_html(
    *,
    run_dir: Path,
    title: str,
    summary_text: str,
    rows: list[dict[str, Any]],
) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #f3efe7;
      --panel: rgba(255, 255, 255, 0.88);
      --ink: #171412;
      --muted: #6b625c;
      --accent: #8f3f23;
      --border: #d7cbc0;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: radial-gradient(circle at top left, #f9e8c8, #f0efe9 55%, #dbe8e2 100%); color: var(--ink); font-family: "Avenir Next", "Segoe UI", sans-serif; }}
    main {{ max-width: 1200px; margin: 0 auto; padding: 32px 20px 80px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 20px; padding: 20px; margin-bottom: 20px; backdrop-filter: blur(8px); box-shadow: 0 12px 30px rgba(60, 42, 24, 0.08); }}
    .summary {{ font-size: 1.05rem; color: var(--muted); }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 12px; border-bottom: 1px solid var(--border); text-align: left; }}
    th {{ background: rgba(143, 63, 35, 0.08); }}
    a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <main>
    <div class="panel">
      <h1>{html.escape(title)}</h1>
      <p class="summary">{html.escape(summary_text)}</p>
    </div>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>Video</th>
            <th>PySceneDetect Cuts</th>
            <th>Gemini Cuts</th>
            <th>Matched Boundaries</th>
            <th>Precision</th>
            <th>Recall</th>
            <th>F1</th>
            <th>Status</th>
            <th>Report</th>
          </tr>
        </thead>
        <tbody>
          {build_overview_rows(run_dir=run_dir, rows=rows)}
        </tbody>
      </table>
    </div>
  </main>
</body>
</html>
"""


def build_pairwise_nav(run_dir: Path, video_results: list[PairwiseVideoResult]) -> str:
    cards = []
    for bundle in video_results:
        status_class = "diff" if bundle.comparison.differing else "match"
        status_text = "DIFF" if bundle.comparison.differing else "MATCH"
        single_report_rel = relative_path(
            run_dir, run_dir / bundle.video_slug / "report.html"
        )
        cards.append(
            "<div class='nav-card-wrap'>"
            + "<a class='nav-card' href='#"
            + html.escape(bundle.video_slug)
            + "'>"
            + f"<strong>{html.escape(bundle.comparison.video_name)}</strong>"
            + f"<span class='nav-meta {status_class}'>{status_text}</span>"
            + f"<span class='nav-sub'>F1 {bundle.comparison.f1_score:.3f} | "
            + f"cuts {bundle.comparison.pyscenedetect_cut_count} vs "
            + f"{bundle.comparison.gemini_cut_count}</span>"
            + "</a>"
            + f"<a class='inline-link' href='{html.escape(single_report_rel)}'>open single-page report</a>"
            + "</div>"
        )
    return "\n".join(cards)


def build_pairwise_method_block(
    *,
    page_dir: Path,
    heading: str,
    subheading: str,
    method_result: MethodResult,
) -> str:
    return f"""
    <div class="method-panel">
      <h3>{html.escape(heading)}</h3>
      <p class="muted">{html.escape(subheading)}</p>
      <div class="clip-grid">
        {build_clip_cards(page_dir=page_dir, clips=method_result.clips)}
      </div>
      <details>
        <summary>Cut table</summary>
        <table>
          <thead>
            <tr>
              <th>Cut ID</th>
              <th>Start</th>
              <th>End</th>
              <th>Duration</th>
              <th>Camera Angle</th>
              <th>Transition In</th>
              <th>Transition Out</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {build_cut_rows(method_result.cuts)}
          </tbody>
        </table>
      </details>
      {
        "<ul class='warning-list'>"
        + "".join(f"<li>{html.escape(warning)}</li>" for warning in method_result.warnings)
        + "</ul>"
        if method_result.warnings
        else ""
      }
    </div>
    """


def build_pairwise_video_section_html(
    *,
    page_dir: Path,
    bundle: PairwiseVideoResult,
) -> str:
    comparison = bundle.comparison
    video_name = comparison.video_name
    return f"""
    <section id="{html.escape(bundle.video_slug)}" class="video-section panel">
      <div class="video-head">
        <div>
          <h2>{html.escape(video_name)}</h2>
          <p class="muted">Tolerance {comparison.tolerance_seconds:.1f}s</p>
        </div>
        <span class="badge {'diff' if comparison.differing else 'match'}">{'DIFFERENT' if comparison.differing else 'MATCH'}</span>
      </div>

      <div class="metric-row">
        <div class="metric-card"><strong>{comparison.pyscenedetect_cut_count}</strong><span>PySceneDetect cuts</span></div>
        <div class="metric-card"><strong>{comparison.gemini_cut_count}</strong><span>Gemini cuts</span></div>
        <div class="metric-card"><strong>{comparison.matched_boundary_count}</strong><span>Matched boundaries</span></div>
        <div class="metric-card"><strong>{comparison.f1_score:.3f}</strong><span>Boundary F1</span></div>
      </div>

      <div class="boundary-box">
        <p><strong>Unmatched PySceneDetect boundaries:</strong> {html.escape(build_value_list(comparison.unmatched_pyscenedetect_boundaries))}</p>
        <p><strong>Unmatched Gemini boundaries:</strong> {html.escape(build_value_list(comparison.unmatched_gemini_boundaries))}</p>
        <details>
          <summary>Boundary match table</summary>
          <table>
            <thead>
              <tr>
                <th>PySceneDetect</th>
                <th>Gemini</th>
                <th>Delta (Gemini - PySceneDetect)</th>
              </tr>
            </thead>
            <tbody>
              {build_match_rows(comparison)}
            </tbody>
          </table>
        </details>
      </div>

      <div class="pair-grid">
        {build_pairwise_method_block(
            page_dir=page_dir,
            heading="PySceneDetect",
            subheading=(
                "AdaptiveDetector | "
                f"adaptive_threshold={bundle.pyscenedetect_result.parameters.get('adaptive_threshold')} | "
                f"min_scene_len={bundle.pyscenedetect_result.parameters.get('min_scene_len_frames')} frames | "
                f"window_width={bundle.pyscenedetect_result.parameters.get('window_width')} | "
                f"min_content_val={bundle.pyscenedetect_result.parameters.get('min_content_val')}"
            ),
            method_result=bundle.pyscenedetect_result,
        )}
        {build_pairwise_method_block(
            page_dir=page_dir,
            heading="Gemini 2.5 Pro",
            subheading=(
                f"model={bundle.gemini_result.parameters.get('model')} | "
                f"analysis_fps={bundle.gemini_result.parameters.get('analysis_fps')}"
            ),
            method_result=bundle.gemini_result,
        )}
      </div>
    </section>
    """


def build_pairwise_report_html(
    *,
    run_dir: Path,
    title: str,
    summary_text: str,
    video_results: list[PairwiseVideoResult],
) -> str:
    sections = "\n".join(
        build_pairwise_video_section_html(page_dir=run_dir, bundle=bundle)
        for bundle in video_results
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --ink: #1a1613;
      --muted: #675f58;
      --paper: rgba(255, 250, 245, 0.94);
      --panel: rgba(255, 255, 255, 0.94);
      --border: #d9cfc5;
      --accent: #8a4b2a;
      --accent-soft: #f3e4d7;
      --mint: #dcefe7;
      --match: #166534;
      --diff: #b91c1c;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, #f9e1bd 0%, transparent 30%),
        radial-gradient(circle at top right, #dcefe7 0%, transparent 35%),
        linear-gradient(180deg, #f5efe7 0%, #ece8e0 100%);
    }}
    main {{ max-width: 1540px; margin: 0 auto; padding: 28px 18px 80px; }}
    .panel {{
      background: var(--paper);
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 20px;
      margin-bottom: 22px;
      box-shadow: 0 14px 34px rgba(73, 49, 29, 0.08);
      backdrop-filter: blur(6px);
    }}
    h1, h2, h3, h4 {{ margin: 0 0 12px; }}
    p, li, td, th, summary, span {{ line-height: 1.45; }}
    a {{ color: var(--accent); }}
    .summary {{ color: var(--muted); max-width: 980px; }}
    .top-links {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }}
    .top-links a {{
      text-decoration: none;
      padding: 8px 12px;
      border-radius: 999px;
      background: var(--accent-soft);
      border: 1px solid #e3cdbb;
    }}
    .nav-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
      gap: 14px;
      margin-top: 18px;
    }}
    .nav-card-wrap {{
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .nav-card {{
      display: flex;
      flex-direction: column;
      gap: 8px;
      text-decoration: none;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 14px;
    }}
    .nav-meta {{
      display: inline-block;
      width: fit-content;
      padding: 4px 9px;
      border-radius: 999px;
      color: #fff;
      font-size: 0.85rem;
      font-weight: 600;
    }}
    .nav-meta.match {{ background: var(--match); }}
    .nav-meta.diff {{ background: var(--diff); }}
    .nav-sub {{ color: var(--muted); font-size: 0.95rem; }}
    .inline-link {{
      display: inline-block;
      width: fit-content;
      font-size: 0.92rem;
      color: var(--accent);
    }}
    .video-section {{ scroll-margin-top: 20px; }}
    .video-head {{
      display: flex;
      justify-content: space-between;
      align-items: start;
      gap: 16px;
      margin-bottom: 16px;
    }}
    .muted {{ color: var(--muted); }}
    .badge {{
      display: inline-block;
      padding: 5px 10px;
      border-radius: 999px;
      color: #fff;
      font-size: 0.9rem;
      font-weight: 700;
      white-space: nowrap;
    }}
    .badge.match {{ background: var(--match); }}
    .badge.diff {{ background: var(--diff); }}
    .metric-row {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px;
      margin-bottom: 16px;
    }}
    .metric-card {{
      background: var(--mint);
      border: 1px solid #c7ddd3;
      border-radius: 18px;
      padding: 14px;
    }}
    .metric-card strong {{ display: block; font-size: 1.5rem; margin-bottom: 6px; }}
    .boundary-box {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 16px;
      margin-bottom: 18px;
    }}
    .pair-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
      align-items: start;
    }}
    .method-panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 16px;
    }}
    .clip-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-bottom: 12px;
    }}
    .clip-card {{
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 10px;
    }}
    .clip-card h4 {{ font-size: 0.98rem; }}
    .clip-card video {{
      width: 100%;
      border-radius: 10px;
      background: #000;
    }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }}
    th {{ background: rgba(138, 75, 42, 0.08); }}
    summary {{
      cursor: pointer;
      font-weight: 600;
      color: var(--accent);
    }}
    details[open] summary {{ margin-bottom: 10px; }}
    .warning-list {{ margin: 12px 0 0; padding-left: 18px; color: #92400e; }}
    @media (max-width: 980px) {{
      .pair-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <div class="panel">
      <h1>{html.escape(title)}</h1>
      <p class="summary">{html.escape(summary_text)}</p>
      <div class="top-links">
        <a href="comparison_report.html">summary table</a>
        <a href="differing_videos_report.html">differing summary table</a>
        <a href="pairwise_comparison_report.html">pairwise full report</a>
        <a href="pairwise_differing_report.html">pairwise differing only</a>
      </div>
      <div class="nav-grid">
        {build_pairwise_nav(run_dir, video_results)}
      </div>
    </div>
    {sections}
  </main>
</body>
</html>
"""


def write_reports(
    *,
    run_dir: Path,
    video_reports: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
    pairwise_video_results: list[PairwiseVideoResult],
    boundary_tolerance: float,
) -> None:
    differing_rows = [row for row in comparison_rows if row["differing"]]
    exact_rows = [row for row in comparison_rows if not row["differing"]]
    differing_pairwise = [
        bundle for bundle in pairwise_video_results if bundle.comparison.differing
    ]
    average_f1 = average([float(row["f1_score"]) for row in comparison_rows])
    average_pyscene_cuts = average(
        [float(row["pyscenedetect_cut_count"]) for row in comparison_rows]
    )
    average_gemini_cuts = average(
        [float(row["gemini_cut_count"]) for row in comparison_rows]
    )

    write_text(
        run_dir / "comparison_report.html",
        build_batch_report_html(
            run_dir=run_dir,
            title="Cut Comparison Report",
            summary_text=(
                f"Compared {len(comparison_rows)} videos with boundary tolerance "
                f"{boundary_tolerance:.1f}s. Exact matches: {len(exact_rows)}. "
                f"Differing videos: {len(differing_rows)}. "
                f"Average PySceneDetect cuts: {average_pyscene_cuts:.2f}. "
                f"Average Gemini cuts: {average_gemini_cuts:.2f}. "
                f"Average boundary F1: {average_f1:.3f}."
            ),
            rows=comparison_rows,
        ),
    )
    write_text(
        run_dir / "differing_videos_report.html",
        build_batch_report_html(
            run_dir=run_dir,
            title="Differing Videos Report",
            summary_text=(
                f"Only videos where Gemini and PySceneDetect boundary sets differ within "
                f"{boundary_tolerance:.1f}s tolerance are shown here. "
                f"Count: {len(differing_rows)} / {len(comparison_rows)}."
            ),
            rows=differing_rows,
        ),
    )
    write_text(
        run_dir / "pairwise_comparison_report.html",
        build_pairwise_report_html(
            run_dir=run_dir,
            title="Pairwise Cut Comparison Report",
            summary_text=(
                f"All compared videos in one page. Boundary tolerance "
                f"{boundary_tolerance:.1f}s. Exact matches: {len(exact_rows)}. "
                f"Differing videos: {len(differing_rows)}. "
                f"Average PySceneDetect cuts: {average_pyscene_cuts:.2f}. "
                f"Average Gemini cuts: {average_gemini_cuts:.2f}. "
                f"Average boundary F1: {average_f1:.3f}."
            ),
            video_results=pairwise_video_results,
        ),
    )
    write_text(
        run_dir / "pairwise_differing_report.html",
        build_pairwise_report_html(
            run_dir=run_dir,
            title="Pairwise Differing Videos Report",
            summary_text=(
                f"Only videos with different boundary sets are included here. "
                f"Count: {len(differing_pairwise)} / {len(pairwise_video_results)} "
                f"within {boundary_tolerance:.1f}s tolerance."
            ),
            video_results=differing_pairwise,
        ),
    )
    write_json(
        run_dir / "aggregate_stats.json",
        {
            "compared_video_count": len(comparison_rows),
            "exact_match_video_count": len(exact_rows),
            "differing_video_count": len(differing_rows),
            "boundary_tolerance_seconds": boundary_tolerance,
            "average_f1_score": round(average_f1, 4),
            "average_pyscenedetect_cut_count": round(average_pyscene_cuts, 4),
            "average_gemini_cut_count": round(average_gemini_cuts, 4),
            "videos": comparison_rows,
        },
    )


def main() -> int:
    args = parse_args()
    if args.report_run_dir is not None:
        run_dir = args.report_run_dir.resolve()
        if not run_dir.is_dir():
            raise SystemExit(f"Run directory not found: {run_dir}")
        pairwise_video_results, comparison_rows, boundary_tolerance = load_pairwise_video_results(
            run_dir
        )
        video_reports = [
            {
                "video_name": bundle.comparison.video_name,
                "report_path": str(run_dir / bundle.video_slug / "report.html"),
                "differing": bundle.comparison.differing,
            }
            for bundle in pairwise_video_results
        ]
        write_reports(
            run_dir=run_dir,
            video_reports=video_reports,
            comparison_rows=comparison_rows,
            pairwise_video_results=pairwise_video_results,
            boundary_tolerance=boundary_tolerance,
        )
        print(f"Rebuilt aggregate reports in {run_dir}")
        return 0

    videos = resolve_videos(args)
    if not videos:
        raise SystemExit("No videos found to analyze.")

    run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = args.output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        run_dir / "run_manifest.json",
        {
            "run_id": run_id,
            "video_count": len(videos),
            "videos": [str(path) for path in videos],
            "detector": "AdaptiveDetector",
            "adaptive_threshold": args.threshold,
            "min_scene_len_frames": args.min_scene_len,
            "window_width": args.window_width,
            "min_content_val": args.min_content_val,
            "gemini_model": args.gemini_model,
            "prompt_path": str(args.prompt_path.resolve()),
            "skip_gemini": args.skip_gemini,
            "skip_clips": args.skip_clips,
            "dry_run_gemini": args.dry_run_gemini,
        },
    )

    comparison_rows: list[dict[str, Any]] = []
    video_reports: list[dict[str, Any]] = []
    pairwise_video_results: list[PairwiseVideoResult] = []

    for video_path in videos:
        metadata = read_video_metadata(video_path)
        video_dir = run_dir / slugify(video_path.stem)
        pyscene_dir = video_dir / "pyscenedetect"
        gemini_dir = video_dir / "gemini"
        pyscene_dir.mkdir(parents=True, exist_ok=True)
        gemini_dir.mkdir(parents=True, exist_ok=True)
        print(f"Running PySceneDetect for {video_path.name}")
        pyscenedetect_result = run_pyscenedetect(
            video_path=video_path,
            metadata=metadata,
            output_dir=pyscene_dir,
            threshold=args.threshold,
            min_scene_len=args.min_scene_len,
            window_width=args.window_width,
            min_content_val=args.min_content_val,
        )
        scene_change_candidates = cut_boundaries(pyscenedetect_result.cuts)

        if args.skip_gemini:
            continue

        print(f"Running Gemini for {video_path.name}")
        gemini_result = run_gemini_cut_extraction(
            video_path=video_path,
            metadata=metadata,
            scene_change_candidates=scene_change_candidates,
            output_dir=gemini_dir,
            prompt_path=args.prompt_path.resolve(),
            model=args.gemini_model,
            env_file=args.env_file.resolve(),
            dry_run=args.dry_run_gemini,
        )
        if gemini_result is None:
            continue

        comparison = compare_method_results(
            video_path=video_path,
            pyscenedetect_result=pyscenedetect_result,
            gemini_result=gemini_result,
            tolerance_seconds=args.boundary_tolerance,
        )
        comparison_rows.append(comparison.to_dict())
        write_json(video_dir / "comparison.json", comparison.to_dict())

        if not args.skip_clips:
            print(f"Rendering PySceneDetect clips for {video_path.name}")
            generate_method_clips(
                video_path=video_path,
                method_result=pyscenedetect_result,
                output_dir=pyscene_dir,
            )
            print(f"Rendering Gemini clips for {video_path.name}")
            generate_method_clips(
                video_path=video_path,
                method_result=gemini_result,
                output_dir=gemini_dir,
            )

        report_path = video_dir / "report.html"
        write_text(
            report_path,
            build_video_report_html(
                page_path=report_path,
                video_name=video_path.name,
                pyscenedetect_result=pyscenedetect_result,
                gemini_result=gemini_result,
                comparison=comparison,
            ),
        )
        video_reports.append(
            {
                "video_name": video_path.name,
                "report_path": str(report_path),
                "differing": comparison.differing,
            }
        )
        pairwise_video_results.append(
            PairwiseVideoResult(
                video_slug=slugify(video_path.stem),
                comparison=comparison,
                pyscenedetect_result=pyscenedetect_result,
                gemini_result=gemini_result,
            )
        )

    write_json(
        run_dir / "comparison_summary.json",
        {
            "run_id": run_id,
            "video_count": len(videos),
            "compared_video_count": len(comparison_rows),
            "boundary_tolerance_seconds": args.boundary_tolerance,
            "video_reports": video_reports,
            "videos": comparison_rows,
        },
    )
    if comparison_rows:
        write_reports(
            run_dir=run_dir,
            video_reports=video_reports,
            comparison_rows=comparison_rows,
            pairwise_video_results=pairwise_video_results,
            boundary_tolerance=args.boundary_tolerance,
        )

    print(f"Saved comparison artifacts to {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
