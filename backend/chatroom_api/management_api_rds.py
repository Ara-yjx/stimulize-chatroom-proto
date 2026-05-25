"""HTTP-backed fallback provider that talks to the management API.

Same interface as ``rds.py`` and ``mock_rds.py``, so the rest of the
codebase can swap implementations transparently. Selected only when direct
Postgres is intentionally unavailable and ``MGMT_API_URL`` is set.

This path is read-only by design. Usage accounting must write directly to
Postgres so the billing row is created atomically from the runtime side
without an extra management-API hop.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from chatroom_api import config

logger = logging.getLogger(__name__)

# Conservative HTTP timeout. The management API runs in the same region;
# 5s leaves plenty of headroom while preventing a stuck request from
# blocking the Lambda for its full 30s timeout.
_HTTP_TIMEOUT_SEC = 5


def _headers() -> dict:
    """Build request headers. Bearer is omitted if no token is configured."""
    h = {"Accept": "application/json"}
    if config.MGMT_API_TOKEN:
        h["Authorization"] = f"Bearer {config.MGMT_API_TOKEN}"
    return h


def get_chatroom(chatroom_id: str) -> Optional[dict]:
    """``POST /api/getChatroom/{id}`` — returns same dict shape as ``rds.get_chatroom``.

    Returns ``None`` on 404. Re-raises on any other HTTP error so the
    caller surfaces a 500 to the widget rather than silently treating
    it as "chatroom not found".
    """
    if not config.MGMT_API_URL:
        raise RuntimeError(
            "management_api_rds requires MGMT_API_URL to be set"
        )
    url = f"{config.MGMT_API_URL.rstrip('/')}/api/getChatroom/{chatroom_id}"
    resp = requests.post(url, headers=_headers(), timeout=_HTTP_TIMEOUT_SEC)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    body = resp.json() or {}
    # Management API doesn't currently return ``owner_id`` (it's tied to the
    # bearer-token caller, not surfaced in the response). Fill with None so
    # the dict shape stays stable across providers — callers don't read it
    # anyway in beta.
    return {
        "id": body.get("id"),
        "owner_id": body.get("owner_id"),
        "name": body.get("name"),
        "status": body.get("status"),
        "setting": body.get("setting") or {},
        "created_at": body.get("created_at"),
        "updated_at": body.get("updated_at"),
    }


def write_usage(
    *,
    usage_event_id: str,
    owner_id: int | str,
    chatroom_id: str,
    conversation_id: str,
    session_id: str,
    provider: str,
    model_id: str,
    pricing_key: str,
    input_tokens: int,
    output_tokens: int,
    estimated_cost_usd,
    invoked_at=None,
    raw_usage_json: dict | None = None,
) -> None:
    """Usage writes are unsupported on the management-API fallback path."""
    raise RuntimeError(
        "management_api_rds does not support write_usage; "
        "configure direct Postgres access for billing writes"
    )
