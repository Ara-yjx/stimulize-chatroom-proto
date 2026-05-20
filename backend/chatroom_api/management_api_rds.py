"""HTTP-backed RDS provider that talks to the management API.

Same interface as ``rds.py`` and ``mock_rds.py``, so the rest of the
codebase can swap implementations transparently. Selected when
``USE_MOCK_RDS=false`` and ``MGMT_API_URL`` is set (see
``auth.py::_get_rds`` and friends).

Why HTTP instead of Postgres direct: in beta, the chatroom backend lives
in a different account/network from Stimulize's Postgres. Going through
the management API keeps Lambda out of the Stimulize VPC. In prod, this
provider can be replaced (or kept) depending on whether Lambda gets
direct DB access.

Usage write path is a no-op today: the management API does not expose a
usage-write endpoint (only ``GET /chatrooms/:id/usage`` for queries).
Per-tick token counts are already persisted on the conversation row's
tick events, so no data is lost. A ``POST /internal/usage`` endpoint is
on the prod TODO list (see ``docs/api-management.yml``); once it lands,
``write_usage`` will issue an HTTP write here.
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
    """``GET /chatrooms/{id}`` — returns same dict shape as ``rds.get_chatroom``.

    Returns ``None`` on 404. Re-raises on any other HTTP error so the
    caller surfaces a 500 to the widget rather than silently treating
    it as "chatroom not found".
    """
    if not config.MGMT_API_URL:
        raise RuntimeError(
            "management_api_rds requires MGMT_API_URL to be set"
        )
    url = f"{config.MGMT_API_URL.rstrip('/')}/chatrooms/{chatroom_id}"
    resp = requests.get(url, headers=_headers(), timeout=_HTTP_TIMEOUT_SEC)
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
    chatroom_id: str,
    conversation_id: str,
    session_id: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """No-op for beta — management API has no usage-write endpoint.

    Per-tick token counts are still persisted on the conversation row's
    tick events (see ``tick_handler._make_tick_event``), so usage data is
    not lost; it just isn't aggregated centrally yet. Will become an HTTP
    POST once ``POST /internal/usage`` is added to the management API.
    """
    logger.debug(
        "management_api_rds.write_usage no-op "
        "(chatroom=%s, conversation=%s, session=%s, in=%d, out=%d)",
        chatroom_id,
        conversation_id,
        session_id,
        input_tokens,
        output_tokens,
    )
