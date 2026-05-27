from decimal import Decimal

from chatroom_api.pricing import estimate_cost_usd, resolve_pricing


def test_resolve_pricing_for_default_bedrock_model():
    pricing = resolve_pricing("bedrock", "global.anthropic.claude-sonnet-4-6")
    assert pricing.pricing_key == "bedrock_claude_sonnet_4_6_global_standard"
    assert pricing.input_per_million_usd == Decimal("3.00")
    assert pricing.output_per_million_usd == Decimal("15.00")
    assert pricing.cache_read_input_per_million_usd == Decimal("0.30")
    assert pricing.cache_write_input_per_million_usd == Decimal("3.75")


def test_estimate_cost_usd_uses_separate_input_and_output_rates():
    pricing_key, cost = estimate_cost_usd(
        "bedrock",
        "global.anthropic.claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=200,
    )
    assert pricing_key == "bedrock_claude_sonnet_4_6_global_standard"
    assert cost == Decimal("0.00600000")


def test_estimate_cost_usd_includes_cache_read_and_write_rates():
    pricing_key, cost = estimate_cost_usd(
        "bedrock",
        "global.anthropic.claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=200,
        cache_read_input_tokens=5000,
        cache_write_input_tokens=1000,
    )
    assert pricing_key == "bedrock_claude_sonnet_4_6_global_standard"
    assert cost == Decimal("0.01125000")
