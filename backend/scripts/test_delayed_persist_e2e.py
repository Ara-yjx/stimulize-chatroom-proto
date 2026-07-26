#!/usr/bin/env python3
"""Create a short room and verify delayed AI output is not pre-written.

This script is intentionally opt-in and targets an explicitly named isolated
development table/Lambda. It reads credentials from the existing account file,
soft-deletes the temporary RDS chatroom, and ends the dev conversation after
the assertion to bound inference cost.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import boto3
import requests
from boto3.dynamodb.conditions import Key


def _account(path: Path) -> tuple[str, str]:
    lines = [line.strip() for line in path.read_text().splitlines()]
    values = {lines[index]: lines[index + 1] for index in range(len(lines) - 1)}
    username = values.get("Username")
    password = values.get("Password")
    if not username or not password:
        raise RuntimeError(f"Could not parse Username/Password from {path}")
    return username, password


def _request_json(method: str, url: str, **kwargs) -> dict:
    response = requests.request(method, url, timeout=30, **kwargs)
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{method} {url} returned non-JSON ({response.status_code})") from exc
    if not response.ok:
        raise RuntimeError(f"{method} {url} failed ({response.status_code}): {payload}")
    return payload


def _unwrap_chatroom(payload: dict) -> dict:
    data = payload.get("data", payload)
    return data.get("chatroom", data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-file", required=True, type=Path)
    parser.add_argument("--management-url", required=True)
    parser.add_argument("--chat-api-url", required=True)
    parser.add_argument("--conversation-table", required=True)
    parser.add_argument("--event-table", required=True)
    parser.add_argument("--tick-function", required=True)
    parser.add_argument("--region", default="us-east-2")
    args = parser.parse_args()

    management_url = args.management_url.rstrip("/")
    chat_api_url = args.chat_api_url.rstrip("/")
    username, password = _account(args.account_file)
    login = _request_json(
        "POST",
        f"{management_url}/api/login",
        json={"username": username, "password": password},
    )
    management_token = login["data"]["access_token"]
    management_headers = {"Authorization": management_token}

    chatroom_id: str | None = None
    conversation_id: str | None = None
    ddb = boto3.resource("dynamodb", region_name=args.region)
    event_table = ddb.Table(args.event_table)
    conversation_table = ddb.Table(args.conversation_table)
    lambda_client = boto3.client("lambda", region_name=args.region)

    try:
        unique = uuid4().hex[:10]
        setting = {
            "topic_instruction": "A short automated test conversation.",
            "additional_prompt": (
                "For this test, always answer the latest participant message "
                "with exactly two very short chat bubbles."
            ),
            "ai_personas": [],
            "model_id": "global.anthropic.claude-sonnet-4-6",
            "mimic_human": True,
            "ai_nickname": "",
            "show_avatars": True,
            "temperature": 0.7,
            "simulate_pairing_seconds": 0,
            "timer_min_minutes": None,
            "timer_max_minutes": None,
            "max_duration_seconds": 45,
            "human_count": 1,
            "ai_count": 1,
            "replace_human_with_ai": False,
            "target_human_count": 1,
            "ai_join_strategy": "fixed_ai_count",
            "ai_strategy_value": 1,
            "max_wait_seconds": 0,
        }
        created = _request_json(
            "POST",
            f"{management_url}/api/createChatroom",
            headers=management_headers,
            json={"name": f"Delayed persist E2E {unique}", "setting": setting},
        )
        chatroom_id = _unwrap_chatroom(created)["id"]

        auth = _request_json(
            "POST",
            f"{chat_api_url}/auth/token",
            json={"chatroom_id": chatroom_id},
        )
        token = auth["token"]
        conversation_id = auth["conversation_id"]
        chat_headers = {"Authorization": f"Bearer {token}"}
        _request_json(
            "POST",
            f"{chat_api_url}/chat/send",
            headers=chat_headers,
            json={"message": "Please reply now for the delayed persistence test."},
        )

        # Let the normal five-second silence gate become actionable, then
        # synchronously invoke the isolated dev tick while polling its event
        # table. The heartbeat may race, but the active-tick lease selects one.
        time.sleep(5.5)
        invocation: dict = {}

        def invoke_tick() -> None:
            response = lambda_client.invoke(
                FunctionName=args.tick_function,
                InvocationType="RequestResponse",
                Payload=json.dumps({"conversation_id": conversation_id}).encode(),
            )
            invocation["payload"] = json.loads(response["Payload"].read() or b"{}")

        thread = threading.Thread(target=invoke_tick, daemon=True)
        thread.start()
        observed_ai: list[dict] = []
        first_observed_at_ms: int | None = None
        deadline = time.time() + 38
        while time.time() < deadline:
            items = event_table.query(
                KeyConditionExpression=Key("conversation_id").eq(conversation_id),
                ConsistentRead=True,
            ).get("Items", [])
            observed_ai = [item for item in items if item.get("role") == "ai"]
            observation_ms = int(time.time() * 1000)
            if any(int(item["timestamp"]) > observation_ms + 250 for item in observed_ai):
                raise AssertionError("AI event was pre-written with a future timestamp")
            if observed_ai:
                first_observed_at_ms = observation_ms
                break
            time.sleep(0.25)

        thread.join(timeout=30)
        if thread.is_alive():
            raise RuntimeError("tick invocation did not finish")
        if not observed_ai:
            raise AssertionError(f"no AI message observed; tick result={invocation.get('payload')}")

        for event in observed_ai:
            created_at_ms = int(datetime.fromisoformat(event["created_at"]).timestamp() * 1000)
            if int(event["timestamp"]) > created_at_ms + 1_000:
                raise AssertionError("AI timestamp is later than persistence time")
            if int(event.get("authored_at", event["timestamp"])) > int(event["timestamp"]):
                raise AssertionError("AI authored_at is later than history timestamp")

        history = _request_json(
            "GET",
            f"{chat_api_url}/chat/messages",
            headers=chat_headers,
            params={"after": "0"},
        )
        if not any(event.get("role") == "ai" for event in history.get("events", [])):
            raise AssertionError("stored AI event was not returned by the chat API")

        print(json.dumps({
            "ok": True,
            "chatroom_id": chatroom_id,
            "conversation_id": conversation_id,
            "tick_result": invocation.get("payload"),
            "ai_messages": len(observed_ai),
            "first_observed_at_ms": first_observed_at_ms,
            "timestamps": [int(event["timestamp"]) for event in observed_ai],
            "authored_at": [int(event.get("authored_at", event["timestamp"])) for event in observed_ai],
        }, indent=2))
    finally:
        if conversation_id:
            conversation_table.update_item(
                Key={"conversation_id": conversation_id},
                UpdateExpression="SET #status = :ended",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":ended": "ended"},
            )
        if chatroom_id:
            _request_json(
                "POST",
                f"{management_url}/api/deleteChatroom/{chatroom_id}",
                headers=management_headers,
                json={},
            )


if __name__ == "__main__":
    main()
