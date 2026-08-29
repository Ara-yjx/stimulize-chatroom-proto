"""HTTP client for Stimulize prepaid credit check and debit.

Used by the tick handler to gate Bedrock on remaining balance and to
dual-write a ledger debit after a successful ``rds.write_usage``.
"""

from __future__ import annotations

import logging

import requests

from chatroom_api import config

logger = logging.getLogger(__name__)

# Match management_api_rds: short enough that a hung Stimulize host cannot
# consume the full Lambda budget, long enough for a same-region hop.
_HTTP_TIMEOUT_SEC = 5


def _headers() -> dict:
    """Build request headers. Bearer is omitted if no token is configured."""
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if config.STIMULIZE_API_TOKEN:
        headers["Authorization"] = f"Bearer {config.STIMULIZE_API_TOKEN}"
    return headers


def _url(path: str) -> str:
    return f"{config.STIMULIZE_API_URL.rstrip('/')}{path}"


def check_credits(owner_id) -> bool:
    """Return True only when Stimulize reports the owner may run inference.

    Fail closed when the API is configured: 404 (owner missing), 401/503,
    unexpected status, and network errors all return False so Bedrock is
    skipped. ``allowed: false`` on 200 is also a deny.
    """
    url = _url("/api/internal/credits/check")
    try:
        resp = requests.post(
            url,
            json={"owner_id": owner_id},
            headers=_headers(),
            timeout=_HTTP_TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        logger.warning("credits check failed: %s", exc)
        return False

    if resp.status_code == 200:
        try:
            body = resp.json() or {}
        except ValueError:
            logger.warning("credits check: invalid JSON on 200; failing closed")
            return False
        return bool(body.get("allowed"))

    if resp.status_code == 404:
        logger.warning("credits check: owner %s not found", owner_id)
        return False

    if resp.status_code in (401, 503):
        logger.warning(
            "credits check unavailable (status=%s); failing closed",
            resp.status_code,
        )
        return False

    logger.warning(
        "credits check unexpected status %s; failing closed",
        resp.status_code,
    )
    return False


def debit_usage(
    *,
    owner_id,
    usage_event_id: str,
    estimated_cost_usd,
    chatroom_id: str | None = None,
    conversation_id: str | None = None,
) -> None:
    """POST a usage debit. Raises on HTTP/network failure for the caller to log."""
    url = _url("/api/internal/credits/debit")
    resp = requests.post(
        url,
        json={
            "owner_id": owner_id,
            "usage_event_id": usage_event_id,
            "estimated_cost_usd": str(estimated_cost_usd),
            "chatroom_id": chatroom_id,
            "conversation_id": conversation_id,
        },
        headers=_headers(),
        timeout=_HTTP_TIMEOUT_SEC,
    )
    resp.raise_for_status()
