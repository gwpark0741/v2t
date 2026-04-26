from __future__ import annotations

import time
from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .agent_a import build_agent_a_request, validate_agent_a_response
from .gemini_metrics import estimate_model_cost_usd, extract_token_usage
from .gemini_client import DEFAULT_AGENT_A_MODEL, create_gemini_client
from .models import AgentARequest, AgentAResponse, FullVideoAssetResult, TokenUsage


DEFAULT_AGENT_A_SYSTEM_PROMPT = (
    "You are an expert in video analysis for sound generation.\n"
    "Watch the full video and build a hierarchical entity registry for downstream sound-action mapping.\n"
    "Base all decisions on visual information only. Do not infer from audio.\n"
    "Hierarchy is strictly 2 levels: entity -> child.\n"
    "Exclude all mouth/throat-produced vocalization sources.\n"
    "Return valid JSON only. No text outside the JSON block."
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
    model: str | None = None
    latency_ms: float = 0.0
    usage: TokenUsage = Field(default_factory=TokenUsage)
    estimated_cost_usd: float = 0.0

    model_config = ConfigDict(extra="forbid", frozen=True)


class _AgentAEntityChildSchema(BaseModel):
    id: str
    label: str

    model_config = ConfigDict(extra="forbid")


class _AgentAEntitySchema(BaseModel):
    id: str
    label: str
    children: list[_AgentAEntityChildSchema] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class _AgentAAmbienceSchema(BaseModel):
    id: str
    label: str

    model_config = ConfigDict(extra="forbid")


class _AgentAUnknownSchema(BaseModel):
    id: str
    label: str
    visual_description: str

    model_config = ConfigDict(extra="forbid")


class _AgentAEntityRegistrySchema(BaseModel):
    entities: list[_AgentAEntitySchema]
    ambience: list[_AgentAAmbienceSchema]
    unknowns: list[_AgentAUnknownSchema]

    model_config = ConfigDict(extra="forbid")


def _build_agent_a_prompt(request: AgentARequest, prompt_header: str) -> str:
    return (
        f"{prompt_header}\n\n"
        "Entities\n\n"
        "An entity is a scene-relevant object, person, group, or environmental source\n"
        "that can produce sound or own sound-producing sub-entities.\n\n"
        "Children represent acoustically distinct sub-sources that would each become\n"
        "an independent sound track downstream.\n"
        "Create children only when genuinely useful.\n\n"
        "Special Cases\n\n"
        "Ambience entries belong in the ambience array and never have children.\n"
        "Unknown entries belong in the unknowns array with a visual_description.\n"
        "Unknown IDs must follow unknown_1, unknown_2, ...\n"
        "Use snake_case IDs and keep them globally unique.\n"
        "Omit the children field entirely for childless entities.\n\n"
        "Authoritative video metadata:\n"
        f"- fps: {request.video_metadata.fps}\n"
        f"- duration_seconds: {request.video_metadata.duration_seconds}\n"
        f"- resolution: {request.video_metadata.width}x{request.video_metadata.height}\n"
    )


def _build_generation_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=_AgentAEntityRegistrySchema.model_json_schema(),
    )


def run_agent_a_runtime(
    full_video_asset: FullVideoAssetResult,
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

    request = build_agent_a_request(full_video_asset=full_video_asset)
    prompt = _build_agent_a_prompt(request, prompt_header)
    video_part = types.Part.from_uri(
        file_uri=request.video_url,
        mime_type=request.video_mime_type,
    )

    started_at = time.monotonic()
    gemini_response = runtime_client.models.generate_content(
        model=model,
        contents=[video_part, prompt],
        config=_build_generation_config(),
    )
    latency_ms = (time.monotonic() - started_at) * 1000.0
    usage = extract_token_usage(gemini_response)

    raw_response_text = gemini_response.text
    if not raw_response_text:
        raise AgentAResponseParseError("Gemini response did not include response.text")

    try:
        entity_registry = _AgentAEntityRegistrySchema.model_validate_json(raw_response_text)
        parsed_response = AgentAResponse(entity_registry=entity_registry.model_dump())
    except ValidationError as exc:
        raise AgentAResponseParseError("Failed to parse Agent A response JSON") from exc

    issues = validate_agent_a_response(full_video_asset, parsed_response)
    if issues:
        raise AgentAPostValidationError(issues)

    return AgentARuntimeOutput(
        request=request,
        response=parsed_response,
        raw_response_text=raw_response_text,
        model=model,
        latency_ms=latency_ms,
        usage=usage,
        estimated_cost_usd=estimate_model_cost_usd(model, usage),
    )
