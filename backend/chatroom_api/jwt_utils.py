"""JWT sign/verify (HS256) with Secrets Manager fallback to env var."""

import logging
import time

import jwt

from chatroom_api import config

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_TTL_SECONDS = 10800  # 3 hours

_cached_secret = None  # type: str | None


def _get_secret() -> str:
    """Try AWS Secrets Manager first (JWT_SECRET_ARN), fall back to JWT_SECRET env var."""
    global _cached_secret
    if _cached_secret is not None:
        return _cached_secret

    if config.JWT_SECRET_ARN:
        try:
            import boto3
            client = boto3.client("secretsmanager")
            resp = client.get_secret_value(SecretId=config.JWT_SECRET_ARN)
            _cached_secret = resp["SecretString"]
            return _cached_secret
        except Exception:
            logger.warning("Failed to read JWT secret from Secrets Manager, falling back to env var")

    _cached_secret = config.JWT_SECRET
    return _cached_secret


def create_token(
    session_id: str,
    conversation_id: str,
    chatroom_id: str,
) -> str:
    """Sign a JWT with HS256 containing session/conversation/chatroom claims."""
    now = int(time.time())
    payload = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "chatroom_id": chatroom_id,
        "iat": now,
        "exp": now + _TTL_SECONDS,
    }
    return jwt.encode(payload, _get_secret(), algorithm=_ALGORITHM)


def verify_token(token: str) -> dict:
    """Decode and validate a JWT. Returns claims dict.

    Raises:
        jwt.ExpiredSignatureError: if the token has expired.
        jwt.InvalidTokenError: for any other validation failure.
    """
    return jwt.decode(token, _get_secret(), algorithms=[_ALGORITHM])
