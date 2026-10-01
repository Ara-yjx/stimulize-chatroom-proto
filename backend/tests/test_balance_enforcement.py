"""Credit enforcement is optional; recording incurred costs is independent."""

import os
import subprocess
import sys
from unittest.mock import Mock

import pytest

from chatroom_api import config, credits_client, inference_usage
from chatroom_api.ai_batch import worker


@pytest.mark.parametrize("value,expected", [(None, "False"), ("false", "False"), ("true", "True")])
def test_config_enforcement_defaults_off(value, expected):
    env = dict(os.environ)
    env.pop("CHATROOM_BALANCE_ENFORCEMENT_ENABLED", None)
    if value is not None:
        env["CHATROOM_BALANCE_ENFORCEMENT_ENABLED"] = value
    result = subprocess.run(
        [sys.executable, "-c", "from chatroom_api.config import CHATROOM_BALANCE_ENFORCEMENT_ENABLED; print(CHATROOM_BALANCE_ENFORCEMENT_ENABLED)"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("enforce,url,owner,allowed,calls", [
    (False, "https://management.example", "9", True, 0),
    (False, "https://management.example", None, True, 0),
    (True, "", "9", True, 0),
    (True, "https://management.example", None, False, 0),
    (True, "https://management.example", "9", False, 1),
])
def test_gate_matrix(monkeypatch, enforce, url, owner, allowed, calls):
    monkeypatch.setattr(config, "CHATROOM_BALANCE_ENFORCEMENT_ENABLED", enforce)
    monkeypatch.setattr(config, "STIMULIZE_API_URL", url)
    check = Mock(return_value=False)
    monkeypatch.setattr(credits_client, "check_credits", check)
    assert inference_usage.credits_allow(owner) is allowed
    assert check.call_count == calls


@pytest.mark.parametrize("enforce,url,debit_fails", [
    (False, "https://management.example", False),
    (False, "https://management.example", True),
    (False, "", False),
    (True, "https://management.example", False),
])
def test_batch_gate_and_real_usage_path(monkeypatch, caplog, enforce, url, debit_fails):
    monkeypatch.setattr(config, "CHATROOM_BALANCE_ENFORCEMENT_ENABLED", enforce)
    monkeypatch.setattr(config, "STIMULIZE_API_URL", url)
    check = Mock(return_value=False)
    debit = Mock(side_effect=RuntimeError("ledger unavailable") if debit_fails else None)
    rds = Mock()
    invoke = Mock(return_value={"messages": ["hello"], "input_tokens": 10, "output_tokens": 5})
    monkeypatch.setattr(credits_client, "check_credits", check)
    monkeypatch.setattr(credits_client, "debit_usage", debit)
    monkeypatch.setattr(inference_usage, "get_rds_provider", lambda: rds)
    monkeypatch.setattr(worker, "invoke_speak_tool", invoke)
    monkeypatch.setattr(worker, "_build_request", lambda *a, **kw: (
        "global.anthropic.claude-sonnet-4-6", 0.7, [], [],
    ))
    monkeypatch.setattr(worker.store, "now_ms", lambda: 100)
    batch = {"owner_id": "9", "chatroom_id": "room", "batch_job_id": "batch",
             "settings_snapshot": {}, "deadline_at": 1000}
    conversation = {"conversation_id": "conversation", "next_turn": 0}
    participant = {"role": "ai", "ai_participant_id": "ai-1"}

    if enforce:
        with pytest.raises(worker.TerminalConversationError, match="insufficient credits"):
            worker._invoke_candidate_once(batch, conversation, participant, [], require_message=True, attempt=0)
        check.assert_called_once_with("9")
        invoke.assert_not_called()
        rds.write_usage.assert_not_called()
        debit.assert_not_called()
        return

    assert worker._invoke_candidate_once(
        batch, conversation, participant, [], require_message=True, attempt=0,
    ) == "hello"
    check.assert_not_called()
    invoke.assert_called_once()
    rds.write_usage.assert_called_once()
    if url:
        debit.assert_called_once()
        assert debit.call_args.kwargs["usage_event_id"] == rds.write_usage.call_args.kwargs["usage_event_id"]
        assert debit.call_args.kwargs["estimated_cost_usd"] == rds.write_usage.call_args.kwargs["estimated_cost_usd"]
        if debit_fails:
            assert "credit debit failed" in caplog.text
    else:
        debit.assert_not_called()
