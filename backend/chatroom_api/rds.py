"""RDS chatroom setting + usage read/write.

Provides the same interface as mock_rds.py so the rest of the codebase can
swap implementations via the USE_MOCK_RDS env var.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from chatroom_api import config

logger = logging.getLogger(__name__)

_conn = None


def _get_connection():
    """Lazy-init a PostgreSQL connection."""
    global _conn
    if _conn is None:
        import psycopg2
        _conn = psycopg2.connect(
            host=config.RDS_HOST,
            port=config.RDS_PORT,
            dbname=config.RDS_DATABASE,
            user=config.RDS_USERNAME,
            password=config.RDS_PASSWORD,
        )
        _conn.autocommit = True
    return _conn


def get_chatroom(chatroom_id: str) -> Optional[dict]:
    """Return a chatroom dict by ID, or None if not found."""
    conn = _get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, owner_id, name, status, setting, created_at, updated_at "
            "FROM chatroom WHERE id = %s",
            (chatroom_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "owner_id": row[1],
            "name": row[2],
            "status": row[3],
            "setting": row[4] if isinstance(row[4], dict) else json.loads(row[4]),
            "created_at": row[5].isoformat() if row[5] else None,
            "updated_at": row[6].isoformat() if row[6] else None,
        }


def write_usage(
    chatroom_id: str,
    conversation_id: str,
    session_id: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Insert a usage record into the chatroom_usage table."""
    conn = _get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO chatroom_usage "
            "(chatroom_id, conversation_id, session_id, input_tokens, output_tokens, total_tokens) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (chatroom_id, conversation_id, session_id, input_tokens, output_tokens, input_tokens + output_tokens),
        )
