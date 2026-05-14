"""Tests for chatroom_api.jwt_utils (v2: 3h TTL, no channel_id/nickname)."""

import time
from unittest.mock import patch

import jwt
import pytest

from chatroom_api.jwt_utils import create_token, verify_token


@pytest.fixture
def sample_claims():
    return {
        "session_id": "sess-001",
        "conversation_id": "conv-001",
        "chatroom_id": "cr-001",
    }


def test_create_token_returns_string(sample_claims):
    token = create_token(**sample_claims)
    assert isinstance(token, str)


def test_roundtrip(sample_claims):
    token = create_token(**sample_claims)
    claims = verify_token(token)
    for key, value in sample_claims.items():
        assert claims[key] == value


def test_token_has_3h_ttl(sample_claims):
    token = create_token(**sample_claims)
    claims = verify_token(token)
    assert "iat" in claims
    assert "exp" in claims
    assert claims["exp"] - claims["iat"] == 10800


def test_no_channel_id_or_nickname_in_claims(sample_claims):
    token = create_token(**sample_claims)
    claims = verify_token(token)
    assert "channel_id" not in claims
    assert "nickname" not in claims


def test_expired_token_raises(sample_claims):
    with patch("chatroom_api.jwt_utils.time") as mock_time:
        mock_time.time.return_value = time.time() - 20000
        token = create_token(**sample_claims)

    with pytest.raises(jwt.ExpiredSignatureError):
        verify_token(token)


def test_invalid_token_raises():
    with pytest.raises(jwt.InvalidTokenError):
        verify_token("not.a.valid.token")


def test_tampered_token_raises(sample_claims):
    payload = {**sample_claims, "iat": int(time.time()), "exp": int(time.time()) + 3600}
    bad_token = jwt.encode(payload, "wrong-secret", algorithm="HS256")
    with pytest.raises(jwt.InvalidTokenError):
        verify_token(bad_token)
