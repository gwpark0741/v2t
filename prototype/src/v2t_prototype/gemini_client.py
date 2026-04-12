from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types


DEFAULT_AGENT_A_MODEL = "gemini-2.5-pro"


def create_gemini_client(api_key: Optional[str] = None) -> genai.Client:
    """
    Create a Gemini Developer API client.

    If api_key is None, google-genai will resolve credentials from environment
    variables (for example GEMINI_API_KEY).
    """
    if api_key:
        return genai.Client(api_key=api_key)
    return genai.Client()


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
