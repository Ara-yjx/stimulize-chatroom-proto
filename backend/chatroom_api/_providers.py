"""Shared backend-selection helpers.

Centralizes the "which RDS provider should I use?" logic that previously
lived inline in ``auth.py``, ``close_lobby.py``, and ``tick_handler.py``.
Each caller now imports ``get_rds_provider()`` from here.

Selection order (first match wins):

1. ``USE_MOCK_RDS=true`` (default for local dev) → ``mock_rds``.
2. Any direct Postgres config is present → ``rds``.
3. ``MGMT_API_URL`` is set → ``management_api_rds`` (legacy fallback for
   read-only chatroom lookups when direct Postgres is intentionally absent).
4. Otherwise → ``rds``.

This is intentionally implicit rather than introducing yet another
``RDS_PROVIDER`` env var: the existing flags already encode the intent
and adding a third knob would just create more invalid combinations to
guard against.
"""

from __future__ import annotations

from chatroom_api import config


def get_rds_provider():
    """Return the configured RDS module.

    The returned object exposes ``get_chatroom(chatroom_id)`` and
    ``write_usage(...)``; see ``rds.py`` for the canonical signatures.
    """
    if config.USE_MOCK_RDS:
        from chatroom_api import mock_rds
        return mock_rds
    if config.RDS_HOST or config.RDS_SECRET_ARN or config.RDS_DATABASE:
        from chatroom_api import rds
        return rds
    if config.MGMT_API_URL:
        from chatroom_api import management_api_rds
        return management_api_rds
    from chatroom_api import rds
    return rds
