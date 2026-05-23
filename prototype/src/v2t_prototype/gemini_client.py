from __future__ import annotations

from dataclasses import dataclass, replace
import time
from pathlib import Path
from typing import Any, Optional

from google import genai
from google.genai import types


DEFAULT_GEMINI_PRO_MODEL = "gemini-2.5-pro"
DEFAULT_GEMINI_FLASH_MODEL = "gemini-2.5-flash"
GEMINI_3_1_PRO_PREVIEW_MODEL = "gemini-3.1-pro-preview"
DEFAULT_GEMINI_VIDEO_FPS = 5.0

DEFAULT_AGENT_A_MODEL = DEFAULT_GEMINI_PRO_MODEL
DEFAULT_AGENT_B_MODEL = DEFAULT_GEMINI_PRO_MODEL
DEFAULT_TRACK_JUDGE_MODEL = DEFAULT_GEMINI_FLASH_MODEL


@dataclass(frozen=True)
class GeminiGenerationParams:
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    seed: int | None = None
    max_output_tokens: int | None = None

    def with_overrides(
        self,
        *,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        seed: int | None = None,
        max_output_tokens: int | None = None,
    ) -> "GeminiGenerationParams":
        updates: dict[str, Any] = {}
        if temperature is not None:
            updates["temperature"] = temperature
        if top_p is not None:
            updates["top_p"] = top_p
        if top_k is not None:
            updates["top_k"] = top_k
        if seed is not None:
            updates["seed"] = seed
        if max_output_tokens is not None:
            updates["max_output_tokens"] = max_output_tokens
        return replace(self, **updates) if updates else self

    def to_config_kwargs(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "temperature": self.temperature,
                "top_p": self.top_p,
                "top_k": self.top_k,
                "seed": self.seed,
                "max_output_tokens": self.max_output_tokens,
            }.items()
            if value is not None
        }


def resolve_generation_params(
    generation_params: GeminiGenerationParams | None = None,
    *,
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    seed: int | None = None,
    max_output_tokens: int | None = None,
) -> GeminiGenerationParams:
    base = generation_params or GeminiGenerationParams()
    return base.with_overrides(
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        seed=seed,
        max_output_tokens=max_output_tokens,
    )


def create_gemini_client(api_key: Optional[str] = None) -> genai.Client:
    """
    Create a Gemini Developer API client.

    If api_key is None, google-genai will resolve credentials from environment
    variables (for example GEMINI_API_KEY).
    """
    if api_key:
        return genai.Client(api_key=api_key)
    return genai.Client()


def create_video_part_from_uri(
    *,
    file_uri: str,
    mime_type: str,
    video_fps: float | None = DEFAULT_GEMINI_VIDEO_FPS,
) -> types.Part:
    part = types.Part.from_uri(file_uri=file_uri, mime_type=mime_type)
    if video_fps is None:
        return part
    return part.model_copy(update={"video_metadata": types.VideoMetadata(fps=video_fps)})


def upload_video_file(client: genai.Client, video_path: Path | str) -> types.File:
    """
    Upload one local video file through Gemini Files API and return File metadata.
    """
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {path}")
    return client.files.upload(file=path)


def wait_for_uploaded_file_active(
    client: genai.Client,
    uploaded_file: types.File,
    *,
    timeout_seconds: float = 60.0,
    poll_interval_seconds: float = 1.0,
) -> types.File:
    """
    Poll Files API until the uploaded file reaches ACTIVE state.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be > 0")

    file_name = uploaded_file.name
    if not file_name:
        raise ValueError("Uploaded file did not include a name")

    state = uploaded_file.state
    if state == types.FileState.ACTIVE:
        return uploaded_file
    if state == types.FileState.FAILED:
        raise RuntimeError(f"Uploaded file entered FAILED state: {file_name}")

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        refreshed = client.files.get(name=file_name)
        state = refreshed.state
        if state == types.FileState.ACTIVE:
            return refreshed
        if state == types.FileState.FAILED:
            raise RuntimeError(f"Uploaded file entered FAILED state: {file_name}")
        time.sleep(poll_interval_seconds)

    raise TimeoutError(
        f"Uploaded file did not become ACTIVE within {timeout_seconds:.1f}s: {file_name}"
    )


def get_uploaded_video_url(uploaded_file: types.File) -> str:
    """
    Read the uploaded video URL required for downstream request traceability.
    """
    uri = uploaded_file.uri
    if not uri:
        raise ValueError("Uploaded file did not include a uri")
    return uri


def get_uploaded_file_name(uploaded_file: types.File) -> str:
    """
    Read the uploaded Gemini file name used for polling and traceability.
    """
    name = uploaded_file.name
    if not name:
        raise ValueError("Uploaded file did not include a name")
    return name
