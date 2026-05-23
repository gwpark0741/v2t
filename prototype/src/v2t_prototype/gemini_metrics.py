from __future__ import annotations

from typing import Any

from .models import TokenUsage


PRO_INPUT_PRICE_PER_MILLION_USD_LE_200K = 1.25
PRO_INPUT_PRICE_PER_MILLION_USD_GT_200K = 2.50
PRO_OUTPUT_PRICE_PER_MILLION_USD_LE_200K = 10.00
PRO_OUTPUT_PRICE_PER_MILLION_USD_GT_200K = 15.00
FLASH_INPUT_PRICE_PER_MILLION_USD = 0.30
FLASH_OUTPUT_PRICE_PER_MILLION_USD = 2.50


def extract_token_usage(response: Any) -> TokenUsage:
    usage_metadata = getattr(response, "usage_metadata", None)
    if usage_metadata is None:
        return TokenUsage()

    return TokenUsage(
        prompt_token_count=int(getattr(usage_metadata, "prompt_token_count", 0) or 0),
        candidates_token_count=int(getattr(usage_metadata, "candidates_token_count", 0) or 0),
        total_token_count=int(getattr(usage_metadata, "total_token_count", 0) or 0),
        cached_content_token_count=int(
            getattr(usage_metadata, "cached_content_token_count", 0) or 0
        ),
        thoughts_token_count=int(getattr(usage_metadata, "thoughts_token_count", 0) or 0),
        tool_use_prompt_token_count=int(
            getattr(usage_metadata, "tool_use_prompt_token_count", 0) or 0
        ),
    )


def add_token_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    return TokenUsage(
        prompt_token_count=left.prompt_token_count + right.prompt_token_count,
        candidates_token_count=left.candidates_token_count + right.candidates_token_count,
        total_token_count=left.total_token_count + right.total_token_count,
        cached_content_token_count=left.cached_content_token_count + right.cached_content_token_count,
        thoughts_token_count=left.thoughts_token_count + right.thoughts_token_count,
        tool_use_prompt_token_count=left.tool_use_prompt_token_count + right.tool_use_prompt_token_count,
    )


def estimate_model_cost_usd(model: str, usage: TokenUsage) -> float:
    normalized_model = model.strip().lower()
    prompt_tokens = usage.prompt_token_count
    output_tokens = usage.candidates_token_count

    if normalized_model.startswith("gemini-2.5-pro"):
        if prompt_tokens <= 200_000:
            input_rate = PRO_INPUT_PRICE_PER_MILLION_USD_LE_200K
            output_rate = PRO_OUTPUT_PRICE_PER_MILLION_USD_LE_200K
        else:
            input_rate = PRO_INPUT_PRICE_PER_MILLION_USD_GT_200K
            output_rate = PRO_OUTPUT_PRICE_PER_MILLION_USD_GT_200K
        return (prompt_tokens / 1_000_000.0) * input_rate + (output_tokens / 1_000_000.0) * output_rate

    if normalized_model.startswith("gemini-2.5-flash"):
        return (
            (prompt_tokens / 1_000_000.0) * FLASH_INPUT_PRICE_PER_MILLION_USD
            + (output_tokens / 1_000_000.0) * FLASH_OUTPUT_PRICE_PER_MILLION_USD
        )

    return 0.0
