from __future__ import annotations

from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, ValidationError

from .agent_a import build_agent_a_request, validate_agent_a_response
from .gemini_client import DEFAULT_AGENT_A_MODEL, create_gemini_client
from .models import AgentARequest, AgentAResponse, PreprocessingResult


DEFAULT_AGENT_A_SYSTEM_PROMPT = (
    "You are Agent A in a video-to-sound metadata pipeline.\n"
    "Use the uploaded full video as the primary source of truth.\n"
    "Do not change cut boundaries.\n"
    "Return only a JSON object that matches the provided schema."
)


class AgentARuntimeError(RuntimeError):
    """Base runtime error for Agent A Gemini execution."""


class AgentAResponseParseError(AgentARuntimeError):
    """Gemini output could not be parsed into AgentAResponse."""


class AgentAPostValidationError(AgentARuntimeError):
    """Gemini output was parseable but violated linkage rules."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("Agent A response failed post-validation: " + "; ".join(issues))


class AgentARuntimeOutput(BaseModel):
    request: AgentARequest
    response: AgentAResponse
    raw_response_text: str

    model_config = ConfigDict(extra="forbid", frozen=True)


def _build_agent_a_prompt(request: AgentARequest, prompt_header: str) -> str:
    return (
        f"{prompt_header}\n\n"
        "Task:\n"
        "1) Build entity_registry with characters, key_objects, ambience_sources.\n"
        "2) Keep all IDs deterministic and prefixed as char_/obj_/amb_.\n\n"
        "Authoritative video metadata:\n"
        f"- fps: {request.video_metadata.fps}\n"
        f"- duration_seconds: {request.video_metadata.duration_seconds}\n"
        f"- resolution: {request.video_metadata.width}x{request.video_metadata.height}\n"
    )


def _build_generation_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=AgentAResponse.model_json_schema(),
    )


def run_agent_a_runtime(
    preprocessing: PreprocessingResult,
    *,
    client: Optional[genai.Client] = None,
    model: str = DEFAULT_AGENT_A_MODEL,
    prompt_header: str = DEFAULT_AGENT_A_SYSTEM_PROMPT,
) -> AgentARuntimeOutput:
    """
    End-to-end Agent A runtime:
    call Gemini with the pre-uploaded video_url -> parse JSON -> post-validate.
    """
    runtime_client = client or create_gemini_client()

    request = build_agent_a_request(preprocessing=preprocessing)
    prompt = _build_agent_a_prompt(request, prompt_header)
    video_part = types.Part.from_uri(
        file_uri=request.video_url,
        mime_type=request.video_mime_type,
    )

    gemini_response = runtime_client.models.generate_content(
        model=model,
        contents=[video_part, prompt],
        config=_build_generation_config(),
    )

    raw_response_text = gemini_response.text
    if not raw_response_text:
        raise AgentAResponseParseError("Gemini response did not include response.text")

    try:
        parsed_response = AgentAResponse.model_validate_json(raw_response_text)
    except ValidationError as exc:
        raise AgentAResponseParseError("Failed to parse Agent A response JSON") from exc

    issues = validate_agent_a_response(preprocessing, parsed_response)
    if issues:
        raise AgentAPostValidationError(issues)

    return AgentARuntimeOutput(
        request=request,
        response=parsed_response,
        raw_response_text=raw_response_text,
    )
