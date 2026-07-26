#!/usr/bin/env python3
"""Run a short resumable-conversation E2E against an isolated runtime API."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
import requests


DEFAULT_MANAGEMENT_API = "https://9wr63is7x6.execute-api.us-east-2.amazonaws.com/live"
DEFAULT_RUNTIME_API = "https://ml3vggnmgk.execute-api.us-east-2.amazonaws.com/prod"
DEFAULT_TABLE = "stimulize-chatroom-event-dev-yjx-20260725-conversations"
DEFAULT_TICK_LAMBDA = "stimulize-chatroom-event-dev-yjx-20260725-tick"


def read_account(path: Path) -> tuple[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    values = {}
    for index, line in enumerate(lines[:-1]):
        if line.strip() in {"Username", "Password"}:
            values[line.strip()] = lines[index + 1].strip()
    if not values.get("Username") or not values.get("Password"):
        raise RuntimeError(f"username/password not found in {path}")
    return values["Username"], values["Password"]


def request_json(method: str, url: str, **kwargs) -> tuple[requests.Response, dict]:
    response = requests.request(method, url, timeout=30, **kwargs)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return response, payload


def require_ok(response: requests.Response, payload: dict, operation: str) -> dict:
    if not response.ok:
        raise RuntimeError(
            f"{operation} failed ({response.status_code}): "
            f"{payload.get('error') or payload.get('message') or 'unknown error'}"
        )
    return payload


def management_data(payload: dict):
    data = payload.get("data", payload)
    return data.get("chatroom", data) if isinstance(data, dict) else data


def auth(runtime_api: str, chatroom_id: str, participant_id: str) -> dict:
    response, payload = request_json(
        "POST",
        f"{runtime_api}/auth/token",
        json={"chatroom_id": chatroom_id, "participant_id": participant_id},
    )
    return require_ok(response, payload, "runtime auth")


def poll_until(
    runtime_api: str,
    token: str,
    *,
    want_ai: bool = False,
    want_inactive: bool = False,
    timeout_seconds: int = 45,
    tick_lambda: str,
    conversation_id: str,
) -> tuple[list[dict], str | None]:
    cursor = None
    events: list[dict] = []
    deadline = time.time() + timeout_seconds
    headers = {"Authorization": f"Bearer {token}"}
    lambda_client = boto3.client("lambda", region_name="us-east-2")
    last_tick_at = 0.0
    while time.time() < deadline:
        if time.time() - last_tick_at >= 4:
            lambda_client.invoke(
                FunctionName=tick_lambda,
                InvocationType="RequestResponse",
                Payload=json.dumps({"conversation_id": conversation_id}).encode(),
            )
            last_tick_at = time.time()
        response, payload = request_json(
            "GET",
            f"{runtime_api}/chat/messages",
            headers=headers,
            params={"after": cursor or "0"},
        )
        require_ok(response, payload, "message poll")
        events.extend(payload.get("events") or [])
        cursor = payload.get("next_after") or cursor
        has_ai = any(event.get("role") == "ai" for event in events)
        is_inactive = payload.get("conversation_status") == "inactive"
        if (not want_ai or has_ai) and (not want_inactive or is_inactive):
            return events, cursor
        time.sleep(1)
    raise RuntimeError(
        f"timed out waiting for ai={want_ai}, inactive={want_inactive}; "
        f"events={len(events)}"
    )


def send(runtime_api: str, token: str, message: str) -> None:
    response, payload = request_json(
        "POST",
        f"{runtime_api}/chat/send",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": message},
    )
    require_ok(response, payload, "send")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-file", type=Path, required=True)
    parser.add_argument("--management-api", default=DEFAULT_MANAGEMENT_API)
    parser.add_argument("--runtime-api", default=DEFAULT_RUNTIME_API)
    parser.add_argument("--conversation-table", default=DEFAULT_TABLE)
    parser.add_argument("--tick-lambda", default=DEFAULT_TICK_LAMBDA)
    args = parser.parse_args()
    management_api = args.management_api.rstrip("/")
    runtime_api = args.runtime_api.rstrip("/")
    username, password = read_account(args.account_file)

    login_response, login_payload = request_json(
        "POST",
        f"{management_api}/api/login",
        json={"username": username, "password": password},
    )
    require_ok(login_response, login_payload, "management login")
    management_token = login_payload["data"]["access_token"]
    management_headers = {"Authorization": management_token}

    chatroom_id = None
    conversation_id = None
    participant_id = f"resume-e2e-{int(time.time())}"
    started = time.time()
    try:
        setting = {
            "resumable": True,
            "human_count": 1,
            "ai_count": 1,
            "replace_human_with_ai": False,
            "target_human_count": 1,
            "ai_join_strategy": "fixed_ai_count",
            "ai_strategy_value": 1,
            "max_wait_seconds": 0,
            "mimic_human": False,
            "simulate_pairing_seconds": 0,
            "topic_instruction": "Briefly discuss a favorite way to learn.",
            "additional_prompt": "Reply concisely and remember earlier details.",
            "ai_personas": [{
                "internal_name": "resume_test_ai",
                "nickname": "Alex",
                "persona": "Be warm and concise.",
                "model_id": None,
                "temperature": None,
            }],
            "model_id": "global.anthropic.claude-sonnet-4-6",
            "temperature": 0.3,
            "timer_min_minutes": None,
            "timer_max_minutes": None,
            "max_duration_seconds": 22,
        }
        create_response, create_payload = request_json(
            "POST",
            f"{management_api}/api/createChatroom",
            headers=management_headers,
            json={
                "name": f"Resume E2E {int(time.time())}",
                "status": "active",
                "setting": setting,
            },
        )
        chatroom = management_data(require_ok(
            create_response, create_payload, "create chatroom"
        ))
        chatroom_id = chatroom["id"]

        first = auth(runtime_api, chatroom_id, participant_id)
        conversation_id = first["conversation_id"]
        assert first["episode_number"] == 1 and first["resumed"] is False
        send(runtime_api, first["token"], "I learn best by building small examples.")
        episode1, _ = poll_until(
            runtime_api,
            first["token"],
            want_ai=True,
            want_inactive=True,
            tick_lambda=args.tick_lambda,
            conversation_id=conversation_id,
        )

        second = auth(runtime_api, chatroom_id, participant_id)
        assert second["conversation_id"] == conversation_id
        assert second["episode_number"] == 2 and second["resumed"] is True

        old_response, old_payload = request_json(
            "GET",
            f"{runtime_api}/chat/messages",
            headers={"Authorization": f"Bearer {first['token']}"},
            params={"after": "0"},
        )
        assert old_response.status_code == 409, old_payload

        history_response, history_payload = request_json(
            "GET",
            f"{runtime_api}/chat/history",
            headers={"Authorization": f"Bearer {second['token']}"},
            params={"limit": 100},
        )
        require_ok(history_response, history_payload, "history")
        history_episodes = {
            event.get("episode_number") for event in history_payload.get("events", [])
        }
        assert {1, 2}.issubset(history_episodes), history_episodes

        send(runtime_api, second["token"], "What did I say about how I learn?")
        episode2, _ = poll_until(
            runtime_api,
            second["token"],
            want_ai=True,
            want_inactive=True,
            tick_lambda=args.tick_lambda,
            conversation_id=conversation_id,
        )

        row = boto3.resource("dynamodb", region_name="us-east-2").Table(
            args.conversation_table
        ).get_item(
            Key={"conversation_id": conversation_id}, ConsistentRead=True
        )["Item"]
        assert row["status"] == "inactive"
        assert int(row["episode_count"]) == 2
        assert [episode["status"] for episode in row["episodes"]] == [
            "inactive", "inactive"
        ]
        assert "active_connection_id" not in row
        assert "active_episode_number" not in row
        print(json.dumps({
            "ok": True,
            "chatroom_id": chatroom_id,
            "conversation_id": conversation_id,
            "episode_1_ai_messages": sum(e.get("role") == "ai" for e in episode1),
            "episode_2_ai_messages": sum(e.get("role") == "ai" for e in episode2),
            "old_connection_status": old_response.status_code,
            "episode_count": int(row["episode_count"]),
            "elapsed_seconds": round(time.time() - started, 1),
        }, indent=2))
    finally:
        if conversation_id:
            # Emergency cost fence if the test exits before the 22-second timeout.
            try:
                table = boto3.resource("dynamodb", region_name="us-east-2").Table(
                    args.conversation_table
                )
                item = table.get_item(
                    Key={"conversation_id": conversation_id}, ConsistentRead=True
                ).get("Item") or {}
                if not item:
                    raise RuntimeError("dev conversation disappeared before cleanup")
                episodes = item.get("episodes") or []
                if episodes and episodes[-1].get("status") == "active":
                    episodes[-1]["status"] = "inactive"
                    episodes[-1]["ended_at"] = datetime.now(timezone.utc).isoformat()
                table.update_item(
                    Key={"conversation_id": conversation_id},
                    UpdateExpression=(
                        "SET #status = :inactive, episodes = :episodes "
                        "REMOVE active_connection_id, active_episode_number, "
                        "active_episode_started_at, active_history_start_cursor, "
                        "active_tick_id, active_tick_until"
                    ),
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={
                        ":inactive": "inactive",
                        ":episodes": episodes,
                    },
                    ConditionExpression="attribute_exists(conversation_id)",
                )
            except Exception as exc:  # noqa: BLE001
                print(f"warning: dev conversation cleanup failed: {exc}")
        if chatroom_id:
            try:
                request_json(
                    "POST",
                    f"{management_api}/api/deleteChatroom/{chatroom_id}",
                    headers=management_headers,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"warning: chatroom soft-delete failed: {exc}")


if __name__ == "__main__":
    main()
