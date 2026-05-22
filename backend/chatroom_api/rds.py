"""RDS chatroom setting + usage read/write.

Provides the same interface as mock_rds.py so the rest of the codebase can
swap implementations via the USE_MOCK_RDS env var.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import boto3
import pg8000.dbapi

from chatroom_api import config

logger = logging.getLogger(__name__)

_conn = None
_secret_cache = None


def _load_rds_secret() -> dict:
    """Fetch and cache the optional RDS secret payload."""
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache

    if not config.RDS_SECRET_ARN:
        _secret_cache = {}
        return _secret_cache

    client = boto3.client("secretsmanager")
    resp = client.get_secret_value(SecretId=config.RDS_SECRET_ARN)
    raw = resp.get("SecretString") or "{}"
    _secret_cache = json.loads(raw)
    return _secret_cache


def _connection_params() -> dict:
    """Resolve connection params from env, falling back to Secrets Manager."""
    secret = _load_rds_secret()
    host = config.RDS_HOST or secret.get("host") or ""
    port = config.RDS_PORT or int(secret.get("port") or 5432)
    dbname = (
        config.RDS_DATABASE
        or secret.get("dbname")
        or secret.get("database")
        or secret.get("dbInstanceIdentifier")
        or ""
    )
    username = config.RDS_USERNAME or secret.get("username") or ""
    password = config.RDS_PASSWORD or secret.get("password") or ""
    return {
        "host": host,
        "port": port,
        "database": dbname,
        "user": username,
        "password": password,
    }


def _get_connection():
    """Lazy-init a PostgreSQL connection."""
    global _conn
    if _conn is None:
        params = _connection_params()
        _conn = pg8000.dbapi.connect(**params)
        _conn.autocommit = True
    return _conn


def get_chatroom(chatroom_id: str) -> Optional[dict]:
    """Return a chatroom dict by ID, or None if not found."""
    conn = _get_connection()
    cur = conn.cursor()
    try:
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
    finally:
        cur.close()


def write_usage(
    chatroom_id: str,
    conversation_id: str,
    session_id: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Insert a usage record into the chatroom_usage table."""
    conn = _get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO chatroom_usage "
            "(chatroom_id, conversation_id, session_id, input_tokens, output_tokens, total_tokens) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (chatroom_id, conversation_id, session_id, input_tokens, output_tokens, input_tokens + output_tokens),
        )
    finally:
        cur.close()
