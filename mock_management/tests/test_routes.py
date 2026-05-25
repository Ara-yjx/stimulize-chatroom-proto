import pytest
from app import create_app
from chatroom_config import CHATROOMS, USAGE, accumulate_usage


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def reset_state():
    """Reset chatrooms and usage to seed state before each test."""
    CHATROOMS.clear()
    USAGE.clear()

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    CHATROOMS["scid_test-chatroom-001"] = {
        "id": "scid_test-chatroom-001",
        "owner_id": "owner_default",
        "name": "Test Chatroom",
        "status": "active",
        "setting": {
            "mode": "one_on_one",
            "topic_instruction": "test topic",
            "model_id": "global.anthropic.claude-sonnet-4-6",
            "simulate_pairing_seconds": 5,
            "timer_min_minutes": 5,
            "timer_max_minutes": 10,
        },
        "created_at": now,
        "updated_at": now,
    }
    yield


# --- POST /api/createChatroom ---

def test_create_chatroom(client):
    resp = client.post("/api/createChatroom", json={
        "name": "My Chatroom",
        "setting": {
            "mode": "one_on_one",
            "topic_instruction": "Be nice",
            "model_id": "global.anthropic.claude-sonnet-4-6",
            "simulate_pairing_seconds": 3,
            "timer_min_minutes": None,
            "timer_max_minutes": None,
        },
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["id"].startswith("scid_")
    assert data["name"] == "My Chatroom"
    assert data["status"] == "active"
    assert data["setting"]["topic_instruction"] == "Be nice"
    assert "created_at" in data
    assert "updated_at" in data
    # Should be stored in CHATROOMS
    assert data["id"] in CHATROOMS


# --- POST /api/getChatrooms ---

def test_list_chatrooms(client):
    resp = client.post("/api/getChatrooms")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1
    # Summary should NOT include setting
    assert "setting" not in data[0]
    assert data[0]["id"] == "scid_test-chatroom-001"
    assert data[0]["name"] == "Test Chatroom"
    assert data[0]["status"] == "active"


# --- POST /api/getChatroom/:id ---

def test_get_chatroom(client):
    resp = client.post("/api/getChatroom/scid_test-chatroom-001")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["id"] == "scid_test-chatroom-001"
    assert data["setting"]["mode"] == "one_on_one"
    assert data["setting"]["topic_instruction"] == "test topic"


def test_get_chatroom_not_found(client):
    resp = client.post("/api/getChatroom/scid_nonexistent")
    assert resp.status_code == 404


# --- POST /api/updateChatroom/:id ---

def test_update_chatroom_name(client):
    resp = client.post("/api/updateChatroom/scid_test-chatroom-001", json={
        "name": "Renamed Chatroom",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == "Renamed Chatroom"
    # Setting should be unchanged
    assert data["setting"]["mode"] == "one_on_one"


def test_update_chatroom_status(client):
    resp = client.post("/api/updateChatroom/scid_test-chatroom-001", json={
        "status": "inactive",
    })
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "inactive"


def test_update_chatroom_setting(client):
    resp = client.post("/api/updateChatroom/scid_test-chatroom-001", json={
        "setting": {
            "mode": "group",
            "topic_instruction": "new topic",
            "model_id": "global.anthropic.claude-sonnet-4-6",
            "simulate_pairing_seconds": 0,
            "timer_min_minutes": None,
            "timer_max_minutes": None,
        },
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["setting"]["mode"] == "group"
    assert data["setting"]["topic_instruction"] == "new topic"


def test_update_chatroom_not_found(client):
    resp = client.post("/api/updateChatroom/scid_nonexistent", json={"name": "x"})
    assert resp.status_code == 404


# --- POST /api/deleteChatroom/:id ---

def test_delete_chatroom(client):
    resp = client.post("/api/deleteChatroom/scid_test-chatroom-001")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "deactivated"
    # Should be inactive in store
    assert CHATROOMS["scid_test-chatroom-001"]["status"] == "inactive"


def test_delete_chatroom_not_found(client):
    resp = client.post("/api/deleteChatroom/scid_nonexistent")
    assert resp.status_code == 404


# --- POST /api/getChatroomUsage/:id ---

def test_get_usage_empty(client):
    resp = client.post("/api/getChatroomUsage/scid_test-chatroom-001")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["chatroom_id"] == "scid_test-chatroom-001"
    assert data["totals"]["input_tokens"] == 0
    assert data["totals"]["output_tokens"] == 0
    assert data["totals"]["estimated_cost_usd"] == 0.0
    assert data["series"] == []


def test_get_usage_with_records(client):
    accumulate_usage(chatroom_id="scid_test-chatroom-001", input_tokens=100, output_tokens=50, estimated_cost_usd=0.1)
    accumulate_usage(chatroom_id="scid_test-chatroom-001", input_tokens=200, output_tokens=75, estimated_cost_usd=0.25)
    # Different chatroom — should not be counted
    accumulate_usage(chatroom_id="scid_other", input_tokens=999, output_tokens=999, estimated_cost_usd=9.99)

    resp = client.post("/api/getChatroomUsage/scid_test-chatroom-001")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["totals"]["input_tokens"] == 300
    assert data["totals"]["output_tokens"] == 125
    assert data["totals"]["estimated_cost_usd"] == 0.35


def test_get_usage_with_date_filter(client):
    # Manually insert records with known timestamps
    USAGE.append({
        "usage_event_id": "usage-1",
        "owner_id": "owner_default",
        "chatroom_id": "scid_test-chatroom-001",
        "conversation_id": "conv-1",
        "session_id": "ai-1",
        "provider": "bedrock",
        "model_id": "global.anthropic.claude-sonnet-4-6",
        "pricing_key": "bedrock_claude_sonnet_4_6_global_standard",
        "input_tokens": 100,
        "output_tokens": 50,
        "estimated_cost_usd": 0.12,
        "invoked_at": "2025-01-01T00:00:00+00:00",
        "created_at": "2025-01-01T00:00:00+00:00",
    })
    USAGE.append({
        "usage_event_id": "usage-2",
        "owner_id": "owner_default",
        "chatroom_id": "scid_test-chatroom-001",
        "conversation_id": "conv-2",
        "session_id": "ai-1",
        "provider": "bedrock",
        "model_id": "global.anthropic.claude-sonnet-4-6",
        "pricing_key": "bedrock_claude_sonnet_4_6_global_standard",
        "input_tokens": 200,
        "output_tokens": 75,
        "estimated_cost_usd": 0.31,
        "invoked_at": "2025-06-15T00:00:00+00:00",
        "created_at": "2025-06-15T00:00:00+00:00",
    })

    # Filter: only records from 2025-03-01 onward
    resp = client.post("/api/getChatroomUsage/scid_test-chatroom-001", json={
        "from": "2025-03-01T00:00:00+00:00"
    })
    data = resp.get_json()
    assert data["totals"]["input_tokens"] == 200
    assert data["totals"]["output_tokens"] == 75
    assert data["totals"]["estimated_cost_usd"] == 0.31


def test_get_usage_with_period_series(client):
    accumulate_usage(
        chatroom_id="scid_test-chatroom-001",
        input_tokens=100,
        output_tokens=20,
        estimated_cost_usd=0.10,
        created_at="2025-06-15T12:05:00+00:00",
    )
    accumulate_usage(
        chatroom_id="scid_test-chatroom-001",
        input_tokens=50,
        output_tokens=10,
        estimated_cost_usd=0.04,
        created_at="2025-06-15T12:45:00+00:00",
    )
    accumulate_usage(
        chatroom_id="scid_test-chatroom-001",
        input_tokens=80,
        output_tokens=15,
        estimated_cost_usd=0.07,
        created_at="2025-06-15T13:01:00+00:00",
    )

    resp = client.post("/api/getChatroomUsage/scid_test-chatroom-001", json={
        "period": "hour"
    })
    data = resp.get_json()
    assert data["totals"]["input_tokens"] == 230
    assert len(data["series"]) == 2
    assert data["series"][0]["input_tokens"] == 150
    assert data["series"][1]["input_tokens"] == 80


def test_get_user_usage(client):
    accumulate_usage(
        owner_id="owner_default",
        chatroom_id="scid_test-chatroom-001",
        input_tokens=100,
        output_tokens=20,
        estimated_cost_usd=0.10,
    )
    accumulate_usage(
        owner_id="owner_default",
        chatroom_id="scid_other",
        input_tokens=50,
        output_tokens=10,
        estimated_cost_usd=0.04,
    )
    accumulate_usage(
        owner_id="owner_other",
        chatroom_id="scid_other",
        input_tokens=999,
        output_tokens=999,
        estimated_cost_usd=9.99,
    )

    resp = client.post("/api/getUserUsage", json={"owner_id": "owner_default"})
    data = resp.get_json()
    assert data["scope"] == "user"
    assert data["owner_id"] == "owner_default"
    assert data["totals"]["input_tokens"] == 150
    assert data["totals"]["output_tokens"] == 30
    assert data["totals"]["estimated_cost_usd"] == 0.14


# --- CORS ---

def test_cors_headers(client):
    resp = client.options("/api/getChatrooms", headers={"Origin": "http://localhost:3000"})
    assert "access-control-allow-origin" in {k.lower() for k in resp.headers.keys()}
