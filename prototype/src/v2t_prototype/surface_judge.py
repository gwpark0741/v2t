from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

from google.genai import types

from .gemini_client import create_gemini_client
from .models import Action, SurfaceJudgment, WarningItem


DEFAULT_SURFACE_JUDGE_MODEL = "gemini-2.5-flash"
FLASH_SYSTEM_PROMPT = """You are a surface compatibility judge for sound design.
Your task: determine whether two actions describe acoustically equivalent surface contact.

Rules:
- COMPATIBLE: same physical surface, even if described differently.
- INCOMPATIBLE: different material or surface condition that would produce different audio.
- When uncertain, return INCOMPATIBLE (conservative default).

Respond ONLY with valid JSON. No explanation outside the JSON.
{
  "result": "COMPATIBLE" | "INCOMPATIBLE",
  "reason": "<one sentence>"
}
"""


def normalize_surface(surface: str) -> str:
    """Normalize a surface string for judgment and cache comparison."""
    normalized = surface.lower().strip()
    normalized = re.sub(r"[^a-z0-9\s]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


@dataclass(frozen=True)
class SurfaceJudgmentResult:
    result: Literal["COMPATIBLE", "INCOMPATIBLE"]
    reason: str
    source: Literal[
        "null_both",
        "null_one_side",
        "normalize_match",
        "cache_hit",
        "flash",
        "flash_error",
    ]
    representative_surface: str | None


def _choose_representative_surface(
    surface_a: str | None,
    surface_b: str | None,
) -> str | None:
    candidates = [surface for surface in (surface_a, surface_b) if surface]
    if not candidates:
        return None
    return max(candidates, key=len)


def _build_flash_payload(
    action_a: Action,
    action_b: Action,
    interaction_type: str,
) -> str:
    return json.dumps(
        {
            "interaction_type": interaction_type,
            "action_a": {
                "action_id": action_a.action_id,
                "surface_context": action_a.surface_context,
                "sound_description": action_a.sound_description,
                "observed_visual_description": action_a.observed_visual_description,
            },
            "action_b": {
                "action_id": action_b.action_id,
                "surface_context": action_b.surface_context,
                "sound_description": action_b.sound_description,
                "observed_visual_description": action_b.observed_visual_description,
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def _build_generation_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=FLASH_SYSTEM_PROMPT,
        response_mime_type="application/json",
    )


class SurfaceJudge:
    def __init__(
        self,
        flash_client: Any | None = None,
        model: str = DEFAULT_SURFACE_JUDGE_MODEL,
    ) -> None:
        self._cache: dict[str, SurfaceJudgmentResult] = {}
        self._judgments: list[SurfaceJudgment] = []
        self._warnings: list[WarningItem] = []
        self._flash_call_count = 0
        self._cache_hit_count = 0
        self._per_call_flash_latency_ms: list[float] = []
        self.flash_client = flash_client
        self.model = model

    @property
    def flash_call_count(self) -> int:
        return self._flash_call_count

    @property
    def cache_hit_count(self) -> int:
        return self._cache_hit_count

    @property
    def total_flash_latency_ms(self) -> float:
        return sum(self._per_call_flash_latency_ms)

    @property
    def per_call_flash_latency_ms(self) -> list[float]:
        return list(self._per_call_flash_latency_ms)

    def get_judgments(self) -> list[SurfaceJudgment]:
        return list(self._judgments)

    def get_warnings(self) -> list[WarningItem]:
        return list(self._warnings)

    def judge(
        self,
        action_a: Action,
        action_b: Action,
        interaction_type: str,
    ) -> SurfaceJudgmentResult:
        surface_a = action_a.surface_context
        surface_b = action_b.surface_context

        if surface_a is None and surface_b is None:
            result = SurfaceJudgmentResult(
                result="COMPATIBLE",
                reason="Both actions have null surface_context.",
                source="null_both",
                representative_surface=None,
            )
            self._record(action_a, action_b, interaction_type, result)
            return result

        if surface_a is None or surface_b is None:
            representative = surface_b if surface_a is None else surface_a
            missing_side = "surface_context_a" if surface_a is None else "surface_context_b"
            result = SurfaceJudgmentResult(
                result="COMPATIBLE",
                reason=f"{missing_side} is null; adopting the non-null surface context.",
                source="null_one_side",
                representative_surface=representative,
            )
            self._record(action_a, action_b, interaction_type, result)
            return result

        normalized_a = normalize_surface(surface_a)
        normalized_b = normalize_surface(surface_b)
        if normalized_a == normalized_b:
            result = SurfaceJudgmentResult(
                result="COMPATIBLE",
                reason="Normalized surface_context strings are identical.",
                source="normalize_match",
                representative_surface=_choose_representative_surface(surface_a, surface_b),
            )
            self._record(action_a, action_b, interaction_type, result)
            return result

        cache_key = self._make_cache_key(interaction_type, surface_a, surface_b)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache_hit_count += 1
            result = SurfaceJudgmentResult(
                result=cached.result,
                reason=cached.reason,
                source="cache_hit",
                representative_surface=cached.representative_surface,
            )
            self._record(action_a, action_b, interaction_type, result)
            return result

        result = self._judge_with_flash(action_a, action_b, interaction_type)
        self._cache[cache_key] = SurfaceJudgmentResult(
            result=result.result,
            reason=result.reason,
            source="flash",
            representative_surface=result.representative_surface,
        )
        self._record(action_a, action_b, interaction_type, result)
        return result

    def _judge_with_flash(
        self,
        action_a: Action,
        action_b: Action,
        interaction_type: str,
    ) -> SurfaceJudgmentResult:
        self._flash_call_count += 1
        payload = _build_flash_payload(action_a, action_b, interaction_type)
        started_at = time.monotonic()

        try:
            client = self.flash_client or create_gemini_client()
            self.flash_client = client
            response = client.models.generate_content(
                model=self.model,
                contents=[payload],
                config=_build_generation_config(),
            )
            self._per_call_flash_latency_ms.append((time.monotonic() - started_at) * 1000.0)
        except Exception as exc:
            self._per_call_flash_latency_ms.append((time.monotonic() - started_at) * 1000.0)
            self._warnings.append(
                WarningItem(
                    code="SURFACE_FLASH_ERROR",
                    severity="warning",
                    message="Surface Flash call failed; defaulted to INCOMPATIBLE.",
                    context={
                        "interaction_type": interaction_type,
                        "action_id_a": action_a.action_id,
                        "action_id_b": action_b.action_id,
                        "error": str(exc),
                    },
                )
            )
            return SurfaceJudgmentResult(
                result="INCOMPATIBLE",
                reason="Flash call failed; defaulting to INCOMPATIBLE.",
                source="flash_error",
                representative_surface=_choose_representative_surface(
                    action_a.surface_context,
                    action_b.surface_context,
                ),
            )

        raw_text = response.text or ""
        try:
            parsed_result, reason = self._parse_flash_response(raw_text)
        except ValueError as exc:
            self._warnings.append(
                WarningItem(
                    code="SURFACE_FLASH_PARSE_ERROR",
                    severity="warning",
                    message="Surface Flash response parse failed; defaulted to INCOMPATIBLE.",
                    context={
                        "interaction_type": interaction_type,
                        "action_id_a": action_a.action_id,
                        "action_id_b": action_b.action_id,
                        "raw_response_text": raw_text,
                        "error": str(exc),
                    },
                )
            )
            return SurfaceJudgmentResult(
                result="INCOMPATIBLE",
                reason=str(exc),
                source="flash_error",
                representative_surface=_choose_representative_surface(
                    action_a.surface_context,
                    action_b.surface_context,
                ),
            )

        return SurfaceJudgmentResult(
            result=parsed_result,
            reason=reason,
            source="flash",
            representative_surface=_choose_representative_surface(
                action_a.surface_context,
                action_b.surface_context,
            ),
        )

    def _parse_flash_response(self, raw: str) -> tuple[Literal["COMPATIBLE", "INCOMPATIBLE"], str]:
        try:
            payload = json.loads(raw.strip())
        except json.JSONDecodeError as exc:
            raise ValueError("Flash response parse error; defaulting to INCOMPATIBLE.") from exc

        result = str(payload.get("result", "")).upper()
        if result not in {"COMPATIBLE", "INCOMPATIBLE"}:
            raise ValueError(
                f"Unexpected result value '{result}'; defaulting to INCOMPATIBLE."
            )
        reason = str(payload.get("reason", "")).strip() or "Flash judged the pair."
        return result, reason

    def _make_cache_key(
        self,
        interaction_type: str,
        surface_a: str,
        surface_b: str,
    ) -> str:
        normalized_pair = tuple(sorted([normalize_surface(surface_a), normalize_surface(surface_b)]))
        return f"{interaction_type}|{normalized_pair[0]}|{normalized_pair[1]}"

    def _record(
        self,
        action_a: Action,
        action_b: Action,
        interaction_type: str,
        result: SurfaceJudgmentResult,
    ) -> None:
        self._judgments.append(
            SurfaceJudgment(
                action_id_a=action_a.action_id,
                action_id_b=action_b.action_id,
                surface_context_a=action_a.surface_context,
                surface_context_b=action_b.surface_context,
                interaction_type=interaction_type,
                result=result.result,
                reason=result.reason,
                source=result.source,
                model=self.model if result.source in {"flash", "flash_error"} else None,
            )
        )
