"""Provider-aware token pricing helpers for usage accounting."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


_ONE_MILLION = Decimal("1000000")
_USD_SCALE = Decimal("0.00000001")


@dataclass(frozen=True)
class PricingRecord:
    provider: str
    pricing_key: str
    input_per_million_usd: Decimal
    output_per_million_usd: Decimal


_BEDROCK_PRICING_BY_MODEL_ID = {
    # Claude Sonnet 4.6 in Bedrock global inference profile.
    "global.anthropic.claude-sonnet-4-6": PricingRecord(
        provider="bedrock",
        pricing_key="bedrock_claude_sonnet_4_6_global_standard",
        input_per_million_usd=Decimal("3.00"),
        output_per_million_usd=Decimal("15.00"),
    ),
    # Accept regional aliases for the same model so old saved chatrooms still
    # produce estimates even if they do not use the global profile string.
    "anthropic.claude-sonnet-4-6": PricingRecord(
        provider="bedrock",
        pricing_key="bedrock_claude_sonnet_4_6_global_standard",
        input_per_million_usd=Decimal("3.00"),
        output_per_million_usd=Decimal("15.00"),
    ),
    "us.anthropic.claude-sonnet-4-6": PricingRecord(
        provider="bedrock",
        pricing_key="bedrock_claude_sonnet_4_6_global_standard",
        input_per_million_usd=Decimal("3.00"),
        output_per_million_usd=Decimal("15.00"),
    ),
    "eu.anthropic.claude-sonnet-4-6": PricingRecord(
        provider="bedrock",
        pricing_key="bedrock_claude_sonnet_4_6_global_standard",
        input_per_million_usd=Decimal("3.00"),
        output_per_million_usd=Decimal("15.00"),
    ),
    "jp.anthropic.claude-sonnet-4-6": PricingRecord(
        provider="bedrock",
        pricing_key="bedrock_claude_sonnet_4_6_global_standard",
        input_per_million_usd=Decimal("3.00"),
        output_per_million_usd=Decimal("15.00"),
    ),
    "au.anthropic.claude-sonnet-4-6": PricingRecord(
        provider="bedrock",
        pricing_key="bedrock_claude_sonnet_4_6_global_standard",
        input_per_million_usd=Decimal("3.00"),
        output_per_million_usd=Decimal("15.00"),
    ),
}


def resolve_pricing(provider: str, model_id: str) -> PricingRecord:
    if provider != "bedrock":
        raise ValueError(f"Unsupported provider for pricing: {provider}")

    try:
        return _BEDROCK_PRICING_BY_MODEL_ID[model_id]
    except KeyError as exc:
        raise ValueError(f"No pricing configured for provider={provider} model_id={model_id}") from exc


def estimate_cost_usd(provider: str, model_id: str, input_tokens: int, output_tokens: int) -> tuple[str, Decimal]:
    pricing = resolve_pricing(provider, model_id)
    input_cost = (Decimal(int(input_tokens)) * pricing.input_per_million_usd) / _ONE_MILLION
    output_cost = (Decimal(int(output_tokens)) * pricing.output_per_million_usd) / _ONE_MILLION
    total_cost = (input_cost + output_cost).quantize(_USD_SCALE, rounding=ROUND_HALF_UP)
    return pricing.pricing_key, total_cost
