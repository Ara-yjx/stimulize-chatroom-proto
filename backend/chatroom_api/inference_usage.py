"""Shared credit gate and Bedrock usage persistence."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from chatroom_api import config, credits_client
from chatroom_api._providers import get_rds_provider
from chatroom_api.pricing import estimate_cost_usd, is_unknown_pricing_key


logger = logging.getLogger(__name__)


def credits_allow(owner_id: str | int) -> bool:
    if not config.STIMULIZE_API_URL:
        return True
    return credits_client.check_credits(owner_id)


def record_bedrock_usage(
    *,
    usage_event_id: str,
    owner_id: str | int,
    chatroom_id: str,
    conversation_id: str,
    ai_participant_id: str,
    model_id: str,
    result: dict,
    invoked_at_ms: int,
    extra_raw_usage: dict | None = None,
) -> None:
    input_tokens = int(result.get("input_tokens", 0) or 0)
    output_tokens = int(result.get("output_tokens", 0) or 0)
    cache_read = int(result.get("cache_read_input_tokens", 0) or 0)
    cache_write = int(result.get("cache_write_input_tokens", 0) or 0)
    pricing_key, estimated_cost = estimate_cost_usd(
        "bedrock",
        model_id,
        input_tokens,
        output_tokens,
        cache_read_input_tokens=cache_read,
        cache_write_input_tokens=cache_write,
        allow_unknown=True,
    )
    pricing_estimated = not is_unknown_pricing_key(pricing_key)
    if not pricing_estimated:
        logger.warning("unknown Bedrock pricing for model_id=%s", model_id)
    get_rds_provider().write_usage(
        usage_event_id=usage_event_id,
        owner_id=owner_id,
        chatroom_id=chatroom_id,
        conversation_id=conversation_id,
        session_id=ai_participant_id,
        provider="bedrock",
        model_id=model_id,
        pricing_key=pricing_key,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=estimated_cost,
        invoked_at=datetime.fromtimestamp(invoked_at_ms / 1000, timezone.utc),
        raw_usage_json={
            "bedrock_invoked": True,
            "cache_read_input_tokens": cache_read,
            "cache_write_input_tokens": cache_write,
            "pricing_estimated": pricing_estimated,
            **(extra_raw_usage or {}),
        },
    )
    if config.STIMULIZE_API_URL:
        try:
            credits_client.debit_usage(
                owner_id=owner_id,
                usage_event_id=usage_event_id,
                estimated_cost_usd=estimated_cost,
                chatroom_id=chatroom_id,
                conversation_id=conversation_id,
            )
        except Exception as exc:
            logger.warning("credit debit failed for %s: %s", usage_event_id, exc)
