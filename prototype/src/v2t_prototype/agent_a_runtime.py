from __future__ import annotations

import json
import time
from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .agent_a import build_agent_a_request, validate_agent_a_response
from .entity_registry import get_ambience_targets, get_sfx_targets
from .gemini_metrics import estimate_model_cost_usd, extract_token_usage
from .gemini_client import (
    DEFAULT_AGENT_A_MODEL,
    DEFAULT_GEMINI_VIDEO_FPS,
    GeminiGenerationParams,
    create_video_part_from_uri,
    create_gemini_client,
    resolve_generation_params,
)
from .models import (
    AgentARequest,
    AgentAResponse,
    CutMapping,
    CutSourceMapping,
    EntityRegistry,
    FullVideoAssetResult,
    TokenUsage,
)
from .prompts import load_prompt


DEFAULT_AGENT_A_SYSTEM_PROMPT = load_prompt("agent_a_system.md")
AGENT_A_USER_PROMPT_TEMPLATE = load_prompt("agent_a_user.md")


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


class _AgentACutSourceMappingSchema(BaseModel):
    cut_id: str
    sfx_source_ids: list[str] = Field(default_factory=list)
    ambience_source_ids: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class _AgentAStructuredResponseSchema(BaseModel):
    entities: list[_AgentAEntitySchema]
    ambience: list[_AgentAAmbienceSchema]
    unknowns: list[_AgentAUnknownSchema]
    cut_mapping: list[_AgentACutSourceMappingSchema]

    model_config = ConfigDict(extra="forbid")


def _build_agent_a_prompt(request: AgentARequest) -> str:
    cuts_json = json.dumps(
        [
            {
                "id": cut.id,
                "start_time": cut.start_time,
                "end_time": cut.end_time,
            }
            for cut in request.cuts
        ],
        indent=2,
    )
    return AGENT_A_USER_PROMPT_TEMPLATE.format(
        video_path=request.video_metadata.video_path,
        fps=request.video_metadata.fps,
        duration_seconds=request.video_metadata.duration_seconds,
        width=request.video_metadata.width,
        height=request.video_metadata.height,
        cuts_json=cuts_json,
    )


def _build_generation_config(
    system_prompt: str,
    *,
    temperature: float | None = None,
    generation_params: GeminiGenerationParams | None = None,
) -> types.GenerateContentConfig:
    params = resolve_generation_params(generation_params, temperature=temperature)
    return types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json",
        response_json_schema=_AgentAStructuredResponseSchema.model_json_schema(),
        **params.to_config_kwargs(),
    )


def _parse_agent_a_response(raw_response_text: str) -> AgentAResponse:
    try:
        raw_payload = json.loads(raw_response_text)
    except json.JSONDecodeError as exc:
        raise AgentAResponseParseError("Failed to parse Agent A response JSON") from exc

    if not isinstance(raw_payload, dict):
        raise AgentAResponseParseError("Failed to parse Agent A response JSON")

    try:
        entity_registry = EntityRegistry.model_validate(
            _AgentAEntityRegistrySchema.model_validate(
                {
                    "entities": raw_payload.get("entities"),
                    "ambience": raw_payload.get("ambience"),
                    "unknowns": raw_payload.get("unknowns"),
                }
            ).model_dump()
        )
        cut_mapping = CutMapping(
            mappings=[
                CutSourceMapping.model_validate(mapping.model_dump())
                for mapping in (
                    _AgentACutSourceMappingSchema.model_validate(entry)
                    for entry in raw_payload.get("cut_mapping", [])
                )
            ]
        )
    except ValidationError as exc:
        raise AgentAResponseParseError("Failed to parse Agent A response JSON") from exc

    return AgentAResponse(
        entity_registry=entity_registry,
        cut_mapping=cut_mapping,
    )


def validate_cut_mapping(
    cut_mapping: CutMapping,
    registry: EntityRegistry,
    expected_cut_ids: list[str],
) -> list[str]:
    issues: list[str] = []
    valid_sfx_ids = set(get_sfx_targets(registry).keys())
    valid_ambience_ids = set(get_ambience_targets(registry).keys())
    expected_set = set(expected_cut_ids)

    seen_cut_ids: set[str] = set()
    duplicate_cut_ids: set[str] = set()
    for mapping in cut_mapping.mappings:
        if mapping.cut_id in seen_cut_ids:
            duplicate_cut_ids.add(mapping.cut_id)
        seen_cut_ids.add(mapping.cut_id)

    for duplicate_cut_id in sorted(duplicate_cut_ids):
        issues.append(f"cut_mapping: duplicate cut_id '{duplicate_cut_id}'")

    for cut_id in expected_cut_ids:
        if cut_id not in seen_cut_ids:
            issues.append(f"cut_mapping: missing entry for cut_id '{cut_id}'")

    for cut_id in seen_cut_ids:
        if cut_id not in expected_set:
            issues.append(f"cut_mapping: unexpected cut_id '{cut_id}'")

    for mapping in cut_mapping.mappings:
        if mapping.cut_id in duplicate_cut_ids:
            continue
        for source_id in mapping.sfx_source_ids:
            if source_id not in valid_sfx_ids:
                issues.append(
                    f"cut_mapping[{mapping.cut_id}]: '{source_id}' is not a valid sfx leaf target"
                )
        for source_id in mapping.ambience_source_ids:
            if source_id not in valid_ambience_ids:
                issues.append(
                    f"cut_mapping[{mapping.cut_id}]: '{source_id}' is not a valid ambience target"
                )

    return issues


def run_agent_a_runtime(
    full_video_asset: FullVideoAssetResult,
    *,
    client: Optional[genai.Client] = None,
    model: str = DEFAULT_AGENT_A_MODEL,
    system_prompt: str = DEFAULT_AGENT_A_SYSTEM_PROMPT,
    prompt_header: str | None = None,
    temperature: float | None = None,
    generation_params: GeminiGenerationParams | None = None,
    video_fps: float | None = DEFAULT_GEMINI_VIDEO_FPS,
) -> AgentARuntimeOutput:
    """
    End-to-end Agent A runtime:
    call Gemini with the pre-uploaded video_url -> parse JSON -> post-validate.
    """
    runtime_client = client or create_gemini_client()
    if prompt_header is not None:
        system_prompt = prompt_header

    request = build_agent_a_request(full_video_asset=full_video_asset)
    prompt = _build_agent_a_prompt(request)
    video_part = create_video_part_from_uri(
        file_uri=request.video_url,
        mime_type=request.video_mime_type,
        video_fps=video_fps,
    )

    started_at = time.monotonic()
    gemini_response = runtime_client.models.generate_content(
        model=model,
        contents=[video_part, prompt],
        config=_build_generation_config(
            system_prompt,
            temperature=temperature,
            generation_params=generation_params,
        ),
    )
    latency_ms = (time.monotonic() - started_at) * 1000.0
    usage = extract_token_usage(gemini_response)

    raw_response_text = gemini_response.text
    if not raw_response_text:
        raise AgentAResponseParseError("Gemini response did not include response.text")

    try:
        parsed_response = _parse_agent_a_response(raw_response_text)
    except AgentAResponseParseError:
        raise

    issues = validate_agent_a_response(full_video_asset, parsed_response)
    issues.extend(
        validate_cut_mapping(
            parsed_response.cut_mapping,
            parsed_response.entity_registry,
            [cut.id for cut in request.cuts],
        )
    )
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
