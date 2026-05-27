"""Provider-aware token pricing helpers for usage accounting."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


_ONE_MILLION = Decimal("1000000")
_USD_SCALE = Decimal("0.00000001")
_UNKNOWN_PRICING_PREFIX = "unknown:"


@dataclass(frozen=True)
class PricingRecord:
    provider: str
    pricing_key: str
    input_per_million_usd: Decimal
    output_per_million_usd: Decimal
    cache_read_input_per_million_usd: Decimal = Decimal("0")
    cache_write_input_per_million_usd: Decimal = Decimal("0")


def _record(
    pricing_key: str,
    *,
    input_per_million_usd: str,
    output_per_million_usd: str,
    cache_read_input_per_million_usd: str = "0",
    cache_write_input_per_million_usd: str = "0",
) -> PricingRecord:
    return PricingRecord(
        provider="bedrock",
        pricing_key=pricing_key,
        input_per_million_usd=Decimal(input_per_million_usd),
        output_per_million_usd=Decimal(output_per_million_usd),
        cache_read_input_per_million_usd=Decimal(cache_read_input_per_million_usd),
        cache_write_input_per_million_usd=Decimal(cache_write_input_per_million_usd),
    )


def _register(
    table: dict[str, PricingRecord],
    model_ids: list[str],
    *,
    pricing_key: str,
    input_per_million_usd: str,
    output_per_million_usd: str,
    cache_read_input_per_million_usd: str = "0",
    cache_write_input_per_million_usd: str = "0",
) -> None:
    record = _record(
        pricing_key,
        input_per_million_usd=input_per_million_usd,
        output_per_million_usd=output_per_million_usd,
        cache_read_input_per_million_usd=cache_read_input_per_million_usd,
        cache_write_input_per_million_usd=cache_write_input_per_million_usd,
    )
    for model_id in model_ids:
        table[model_id] = record


# Synced on 2026-05-27 from the official Amazon Bedrock pricing page / AWS
# Pricing API for Standard tier in us-east-2 where available. Anthropic model
# families keep the existing Bedrock write-time estimates used in this repo.
_BEDROCK_PRICING_BY_MODEL_ID: dict[str, PricingRecord] = {}

_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    [
        "global.anthropic.claude-sonnet-4-6",
        "anthropic.claude-sonnet-4-6",
        "us.anthropic.claude-sonnet-4-6",
        "eu.anthropic.claude-sonnet-4-6",
        "jp.anthropic.claude-sonnet-4-6",
        "au.anthropic.claude-sonnet-4-6",
    ],
    pricing_key="bedrock_claude_sonnet_4_6_global_standard",
    input_per_million_usd="3.00",
    output_per_million_usd="15.00",
    cache_read_input_per_million_usd="0.30",
    cache_write_input_per_million_usd="3.75",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    [
        "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "anthropic.claude-sonnet-4-5-20250929-v1:0",
        "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "jp.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "au.anthropic.claude-sonnet-4-5-20250929-v1:0",
    ],
    pricing_key="bedrock_claude_sonnet_4_5_standard",
    input_per_million_usd="3.00",
    output_per_million_usd="15.00",
    cache_read_input_per_million_usd="0.30",
    cache_write_input_per_million_usd="3.75",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    [
        "global.anthropic.claude-sonnet-4-20250514-v1:0",
        "anthropic.claude-sonnet-4-20250514-v1:0",
        "us.anthropic.claude-sonnet-4-20250514-v1:0",
        "eu.anthropic.claude-sonnet-4-20250514-v1:0",
        "jp.anthropic.claude-sonnet-4-20250514-v1:0",
        "au.anthropic.claude-sonnet-4-20250514-v1:0",
    ],
    pricing_key="bedrock_claude_sonnet_4_standard",
    input_per_million_usd="3.00",
    output_per_million_usd="15.00",
    cache_read_input_per_million_usd="0.30",
    cache_write_input_per_million_usd="3.75",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    [
        "global.anthropic.claude-opus-4-7",
        "anthropic.claude-opus-4-7",
        "us.anthropic.claude-opus-4-7",
        "eu.anthropic.claude-opus-4-7",
        "jp.anthropic.claude-opus-4-7",
        "au.anthropic.claude-opus-4-7",
    ],
    pricing_key="bedrock_claude_opus_4_7_standard",
    input_per_million_usd="5.00",
    output_per_million_usd="25.00",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    [
        "global.anthropic.claude-opus-4-6-v1",
        "anthropic.claude-opus-4-6-v1",
        "us.anthropic.claude-opus-4-6-v1",
        "eu.anthropic.claude-opus-4-6-v1",
        "jp.anthropic.claude-opus-4-6-v1",
        "au.anthropic.claude-opus-4-6-v1",
    ],
    pricing_key="bedrock_claude_opus_4_6_standard",
    input_per_million_usd="5.00",
    output_per_million_usd="25.00",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    [
        "global.anthropic.claude-opus-4-5",
        "anthropic.claude-opus-4-5",
        "us.anthropic.claude-opus-4-5",
        "eu.anthropic.claude-opus-4-5",
        "jp.anthropic.claude-opus-4-5",
        "au.anthropic.claude-opus-4-5",
    ],
    pricing_key="bedrock_claude_opus_4_5_standard",
    input_per_million_usd="5.00",
    output_per_million_usd="25.00",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    [
        "global.anthropic.claude-haiku-4-5-20251001-v1:0",
        "anthropic.claude-haiku-4-5-20251001-v1:0",
        "anthropic.claude-haiku-4-5",
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
        "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
        "au.anthropic.claude-haiku-4-5-20251001-v1:0",
    ],
    pricing_key="bedrock_claude_haiku_4_5_standard",
    input_per_million_usd="1.00",
    output_per_million_usd="5.00",
    cache_read_input_per_million_usd="0.10",
    cache_write_input_per_million_usd="1.25",
)

_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["us.amazon.nova-micro-v1:0"],
    pricing_key="bedrock_nova_micro_standard",
    input_per_million_usd="0.035",
    output_per_million_usd="0.14",
    cache_read_input_per_million_usd="0.00875",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["us.amazon.nova-lite-v1:0"],
    pricing_key="bedrock_nova_lite_standard",
    input_per_million_usd="0.06",
    output_per_million_usd="0.24",
    cache_read_input_per_million_usd="0.015",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["us.amazon.nova-pro-v1:0"],
    pricing_key="bedrock_nova_pro_standard",
    input_per_million_usd="0.80",
    output_per_million_usd="3.20",
    cache_read_input_per_million_usd="0.20",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["us.amazon.nova-premier-v1:0"],
    pricing_key="bedrock_nova_premier_standard",
    input_per_million_usd="2.50",
    output_per_million_usd="12.50",
    cache_read_input_per_million_usd="0.625",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["global.amazon.nova-2-lite-v1:0"],
    pricing_key="bedrock_nova_2_lite_standard",
    input_per_million_usd="0.30",
    output_per_million_usd="2.50",
    cache_read_input_per_million_usd="0.0825",
)

_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["us.meta.llama4-maverick-17b-instruct-v1:0"],
    pricing_key="bedrock_llama4_maverick_17b_standard",
    input_per_million_usd="0.24",
    output_per_million_usd="0.97",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["us.meta.llama4-scout-17b-instruct-v1:0"],
    pricing_key="bedrock_llama4_scout_17b_standard",
    input_per_million_usd="0.17",
    output_per_million_usd="0.66",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["us.meta.llama3-3-70b-instruct-v1:0"],
    pricing_key="bedrock_llama3_3_70b_standard",
    input_per_million_usd="0.72",
    output_per_million_usd="0.72",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["us.meta.llama3-1-70b-instruct-v1:0"],
    pricing_key="bedrock_llama3_1_70b_standard",
    input_per_million_usd="0.72",
    output_per_million_usd="0.72",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["us.meta.llama3-1-8b-instruct-v1:0"],
    pricing_key="bedrock_llama3_1_8b_standard",
    input_per_million_usd="0.22",
    output_per_million_usd="0.22",
)

_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["us.deepseek.r1-v1:0"],
    pricing_key="bedrock_deepseek_r1_standard",
    input_per_million_usd="1.35",
    output_per_million_usd="5.40",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["deepseek.v3-v1:0", "deepseek.v3.1"],
    pricing_key="bedrock_deepseek_v3_1_standard",
    input_per_million_usd="0.58",
    output_per_million_usd="1.68",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["deepseek.v3.2"],
    pricing_key="bedrock_deepseek_v3_2_standard",
    input_per_million_usd="0.62",
    output_per_million_usd="1.85",
)

_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["qwen.qwen3-235b-a22b-2507-v1:0"],
    pricing_key="bedrock_qwen3_235b_a22b_2507_standard",
    input_per_million_usd="0.22",
    output_per_million_usd="0.88",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["qwen.qwen3-32b-v1:0"],
    pricing_key="bedrock_qwen3_32b_standard",
    input_per_million_usd="0.15",
    output_per_million_usd="0.60",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["qwen.qwen3-next-80b-a3b"],
    pricing_key="bedrock_qwen3_next_80b_a3b_standard",
    input_per_million_usd="0.14",
    output_per_million_usd="1.20",
)

_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["google.gemma-3-27b-it"],
    pricing_key="bedrock_gemma_3_27b_standard",
    input_per_million_usd="0.23",
    output_per_million_usd="0.38",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["google.gemma-3-12b-it"],
    pricing_key="bedrock_gemma_3_12b_standard",
    input_per_million_usd="0.09",
    output_per_million_usd="0.29",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["google.gemma-3-4b-it"],
    pricing_key="bedrock_gemma_3_4b_standard",
    input_per_million_usd="0.04",
    output_per_million_usd="0.08",
)

_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["mistral.mistral-large-3-675b-instruct"],
    pricing_key="bedrock_mistral_large_3_675b_standard",
    input_per_million_usd="0.50",
    output_per_million_usd="1.50",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["mistral.devstral-2-123b"],
    pricing_key="bedrock_devstral_2_123b_standard",
    input_per_million_usd="0.40",
    output_per_million_usd="2.00",
)
_register(
    _BEDROCK_PRICING_BY_MODEL_ID,
    ["mistral.ministral-3-14b-instruct"],
    pricing_key="bedrock_ministral_3_14b_standard",
    input_per_million_usd="0.20",
    output_per_million_usd="0.20",
)


def _unknown_pricing_key(provider: str, model_id: str) -> str:
    return f"{_UNKNOWN_PRICING_PREFIX}{provider}:{model_id}"


def is_unknown_pricing_key(pricing_key: str) -> bool:
    return pricing_key.startswith(_UNKNOWN_PRICING_PREFIX)


def resolve_pricing(provider: str, model_id: str) -> PricingRecord:
    if provider != "bedrock":
        raise ValueError(f"Unsupported provider for pricing: {provider}")

    try:
        return _BEDROCK_PRICING_BY_MODEL_ID[model_id]
    except KeyError as exc:
        raise ValueError(f"No pricing configured for provider={provider} model_id={model_id}") from exc


def estimate_cost_usd(
    provider: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_read_input_tokens: int = 0,
    cache_write_input_tokens: int = 0,
    allow_unknown: bool = False,
) -> tuple[str, Decimal]:
    try:
        pricing = resolve_pricing(provider, model_id)
    except ValueError:
        if not allow_unknown:
            raise
        return _unknown_pricing_key(provider, model_id), Decimal("0").quantize(_USD_SCALE)

    input_cost = (Decimal(int(input_tokens)) * pricing.input_per_million_usd) / _ONE_MILLION
    output_cost = (Decimal(int(output_tokens)) * pricing.output_per_million_usd) / _ONE_MILLION
    cache_read_cost = (
        Decimal(int(cache_read_input_tokens)) * pricing.cache_read_input_per_million_usd
    ) / _ONE_MILLION
    cache_write_cost = (
        Decimal(int(cache_write_input_tokens)) * pricing.cache_write_input_per_million_usd
    ) / _ONE_MILLION
    total_cost = (input_cost + output_cost + cache_read_cost + cache_write_cost).quantize(
        _USD_SCALE,
        rounding=ROUND_HALF_UP,
    )
    return pricing.pricing_key, total_cost
