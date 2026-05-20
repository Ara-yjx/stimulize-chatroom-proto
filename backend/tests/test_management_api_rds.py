"""Tests for chatroom_api.management_api_rds.

Covers:
- ``get_chatroom`` issues ``GET {MGMT_API_URL}/chatrooms/{id}`` with a
  bearer header and parses the response into the canonical dict shape.
- ``get_chatroom`` returns ``None`` on 404.
- ``get_chatroom`` raises on other HTTP errors (so the widget surfaces
  500 rather than masquerading as "not found").
- ``write_usage`` is a no-op (does not call out — important since the
  management API has no usage-write endpoint today).
- The ``_providers.get_rds_provider`` selector picks this module when
  ``USE_MOCK_RDS=false`` and ``MGMT_API_URL`` is set.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from chatroom_api import config, management_api_rds


SAMPLE_RESPONSE = {
    "id": "scid_test-001",
    "name": "College Chat",
    "status": "active",
    "setting": {
        "mode": "group",
        "topic_instruction": "talk about college",
        "model_id": "global.anthropic.claude-sonnet-4-6",
        "target_human_count": 4,
        "ai_join_strategy": "fixed_ai_count",
        "ai_strategy_value": 1,
        "max_wait_seconds": 60,
    },
    "created_at": "2026-01-01T00:00:00+00:00",
    "updated_at": "2026-01-01T00:00:00+00:00",
}


def _mock_response(status_code: int, json_body: dict | None = None):
    """Build a stub Response that mimics the methods we use."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    if status_code >= 400 and status_code != 404:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f"{status_code} error"
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestGetChatroom:

    def test_issues_get_with_bearer(self):
        with patch.object(config, "MGMT_API_URL", "https://mgmt.example.com"), \
             patch.object(config, "MGMT_API_TOKEN", "tok-123"), \
             patch("chatroom_api.management_api_rds.requests.get") as get_mock:
            get_mock.return_value = _mock_response(200, SAMPLE_RESPONSE)
            result = management_api_rds.get_chatroom("scid_test-001")

        get_mock.assert_called_once()
        args, kwargs = get_mock.call_args
        assert args[0] == "https://mgmt.example.com/chatrooms/scid_test-001"
        assert kwargs["headers"]["Authorization"] == "Bearer tok-123"
        assert kwargs["headers"]["Accept"] == "application/json"
        assert kwargs["timeout"] == 5

        assert result["id"] == "scid_test-001"
        assert result["status"] == "active"
        assert result["setting"]["mode"] == "group"
        assert result["setting"]["target_human_count"] == 4
        # owner_id is filled with None when not in the response
        assert result["owner_id"] is None

    def test_strips_trailing_slash_on_url(self):
        with patch.object(config, "MGMT_API_URL", "https://mgmt.example.com/"), \
             patch.object(config, "MGMT_API_TOKEN", "tok"), \
             patch("chatroom_api.management_api_rds.requests.get") as get_mock:
            get_mock.return_value = _mock_response(200, SAMPLE_RESPONSE)
            management_api_rds.get_chatroom("scid_x")
        url = get_mock.call_args[0][0]
        assert url == "https://mgmt.example.com/chatrooms/scid_x"

    def test_omits_bearer_when_token_unset(self):
        with patch.object(config, "MGMT_API_URL", "https://mgmt.example.com"), \
             patch.object(config, "MGMT_API_TOKEN", ""), \
             patch("chatroom_api.management_api_rds.requests.get") as get_mock:
            get_mock.return_value = _mock_response(200, SAMPLE_RESPONSE)
            management_api_rds.get_chatroom("scid_x")
        headers = get_mock.call_args[1]["headers"]
        assert "Authorization" not in headers

    def test_404_returns_none(self):
        with patch.object(config, "MGMT_API_URL", "https://mgmt.example.com"), \
             patch.object(config, "MGMT_API_TOKEN", "tok"), \
             patch("chatroom_api.management_api_rds.requests.get") as get_mock:
            get_mock.return_value = _mock_response(404)
            result = management_api_rds.get_chatroom("scid_missing")
        assert result is None

    def test_500_raises(self):
        with patch.object(config, "MGMT_API_URL", "https://mgmt.example.com"), \
             patch.object(config, "MGMT_API_TOKEN", "tok"), \
             patch("chatroom_api.management_api_rds.requests.get") as get_mock:
            get_mock.return_value = _mock_response(500)
            with pytest.raises(requests.HTTPError):
                management_api_rds.get_chatroom("scid_x")

    def test_missing_url_raises(self):
        with patch.object(config, "MGMT_API_URL", ""):
            with pytest.raises(RuntimeError):
                management_api_rds.get_chatroom("scid_x")


class TestWriteUsage:

    def test_is_noop(self):
        # Must not issue any HTTP call — the management API has no usage-write endpoint.
        with patch("chatroom_api.management_api_rds.requests.post") as post_mock, \
             patch("chatroom_api.management_api_rds.requests.put") as put_mock, \
             patch("chatroom_api.management_api_rds.requests.get") as get_mock:
            management_api_rds.write_usage(
                "scid_x", "conv-1", "sess-1", input_tokens=10, output_tokens=20
            )
        post_mock.assert_not_called()
        put_mock.assert_not_called()
        get_mock.assert_not_called()


class TestProviderSelection:
    """``_providers.get_rds_provider`` chooses the right backend per config."""

    def test_mock_wins_when_use_mock_rds_true(self):
        from chatroom_api import _providers, mock_rds
        with patch.object(config, "USE_MOCK_RDS", True), \
             patch.object(config, "MGMT_API_URL", "https://mgmt.example.com"):
            assert _providers.get_rds_provider() is mock_rds

    def test_management_api_when_url_set_and_mock_off(self):
        from chatroom_api import _providers
        with patch.object(config, "USE_MOCK_RDS", False), \
             patch.object(config, "MGMT_API_URL", "https://mgmt.example.com"):
            assert _providers.get_rds_provider() is management_api_rds

    def test_falls_back_to_postgres_when_neither_set(self):
        from chatroom_api import _providers, rds
        with patch.object(config, "USE_MOCK_RDS", False), \
             patch.object(config, "MGMT_API_URL", ""):
            assert _providers.get_rds_provider() is rds
