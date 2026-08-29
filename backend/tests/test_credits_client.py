"""Tests for chatroom_api.credits_client.

Covers fail-closed check semantics and the debit request payload.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from chatroom_api import config, credits_client


def _mock_response(status_code: int, json_body: dict | None = None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f"{status_code} error"
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestCheckCredits:

    def test_allowed_true(self):
        with patch.object(config, "STIMULIZE_API_URL", "https://stimulize.example.com"), \
             patch.object(config, "STIMULIZE_API_TOKEN", "tok-123"), \
             patch("chatroom_api.credits_client.requests.post") as post_mock:
            post_mock.return_value = _mock_response(
                200,
                {"allowed": True, "wallet": {"balance_micros": 1, "currency": "usd"}},
            )
            assert credits_client.check_credits(123) is True

        post_mock.assert_called_once()
        args, kwargs = post_mock.call_args
        assert args[0] == "https://stimulize.example.com/api/internal/credits/check"
        assert kwargs["json"] == {"owner_id": 123}
        assert kwargs["headers"]["Authorization"] == "Bearer tok-123"
        assert kwargs["timeout"] == 5

    def test_allowed_false(self):
        with patch.object(config, "STIMULIZE_API_URL", "https://stimulize.example.com"), \
             patch("chatroom_api.credits_client.requests.post") as post_mock:
            post_mock.return_value = _mock_response(200, {"allowed": False})
            assert credits_client.check_credits(123) is False

    def test_404_owner_not_found_is_denied(self):
        with patch.object(config, "STIMULIZE_API_URL", "https://stimulize.example.com"), \
             patch("chatroom_api.credits_client.requests.post") as post_mock:
            post_mock.return_value = _mock_response(404, {"allowed": False})
            assert credits_client.check_credits(123) is False

    def test_401_and_503_fail_closed(self):
        for status in (401, 503):
            with patch.object(config, "STIMULIZE_API_URL", "https://stimulize.example.com"), \
                 patch("chatroom_api.credits_client.requests.post") as post_mock:
                post_mock.return_value = _mock_response(status)
                assert credits_client.check_credits(123) is False

    def test_network_error_fails_closed(self):
        with patch.object(config, "STIMULIZE_API_URL", "https://stimulize.example.com"), \
             patch("chatroom_api.credits_client.requests.post") as post_mock:
            post_mock.side_effect = requests.Timeout("timed out")
            assert credits_client.check_credits(123) is False

    def test_strips_trailing_slash_on_url(self):
        with patch.object(config, "STIMULIZE_API_URL", "https://stimulize.example.com/"), \
             patch("chatroom_api.credits_client.requests.post") as post_mock:
            post_mock.return_value = _mock_response(200, {"allowed": True})
            credits_client.check_credits(1)
        assert post_mock.call_args[0][0] == (
            "https://stimulize.example.com/api/internal/credits/check"
        )


class TestDebitUsage:

    def test_posts_payload_and_raises_on_error(self):
        with patch.object(config, "STIMULIZE_API_URL", "https://stimulize.example.com"), \
             patch.object(config, "STIMULIZE_API_TOKEN", "tok-123"), \
             patch("chatroom_api.credits_client.requests.post") as post_mock:
            post_mock.return_value = _mock_response(200, {"clamped": False})
            credits_client.debit_usage(
                owner_id=123,
                usage_event_id="conv:1:ai",
                estimated_cost_usd="0.001234",
                chatroom_id="scid_1",
                conversation_id="conv-1",
            )

        args, kwargs = post_mock.call_args
        assert args[0] == "https://stimulize.example.com/api/internal/credits/debit"
        assert kwargs["json"] == {
            "owner_id": 123,
            "usage_event_id": "conv:1:ai",
            "estimated_cost_usd": "0.001234",
            "chatroom_id": "scid_1",
            "conversation_id": "conv-1",
        }
        assert kwargs["headers"]["Authorization"] == "Bearer tok-123"
        assert kwargs["timeout"] == 5

    def test_raises_on_http_error(self):
        with patch.object(config, "STIMULIZE_API_URL", "https://stimulize.example.com"), \
             patch("chatroom_api.credits_client.requests.post", return_value=_mock_response(503)), \
             pytest.raises(requests.HTTPError):
            credits_client.debit_usage(
                owner_id=123,
                usage_event_id="conv:1:ai",
                estimated_cost_usd="0.001234",
            )
