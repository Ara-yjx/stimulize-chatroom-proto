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


# --- POST /chatrooms (create) ---

def test_create_chatroom(client):
    resp = client.post("/chatrooms", json={
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


# --- GET /chatrooms (list) ---

def test_list_chatrooms(client):
    resp = client.get("/chatrooms")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1
    # Summary should NOT include setting
    assert "setting" not in data[0]
    assert data[0]["id"] == "scid_test-chatroom-001"
    assert data[0]["name"] == "Test Chatroom"
    assert data[0]["status"] == "active"


# --- GET /chatrooms/:id (get) ---

def test_get_chatroom(client):
    resp = client.get("/chatrooms/scid_test-chatroom-001")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["id"] == "scid_test-chatroom-001"
    assert data["setting"]["mode"] == "one_on_one"
    assert data["setting"]["topic_instruction"] == "test topic"


def test_get_chatroom_not_found(client):
    resp = client.get("/chatrooms/scid_nonexistent")
    assert resp.status_code == 404


# --- PUT /chatrooms/:id (update) ---

def test_update_chatroom_name(client):
    resp = client.put("/chatrooms/scid_test-chatroom-001", json={
        "name": "Renamed Chatroom",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == "Renamed Chatroom"
    # Setting should be unchanged
    assert data["setting"]["mode"] == "one_on_one"


def test_update_chatroom_status(client):
    resp = client.put("/chatrooms/scid_test-chatroom-001", json={
        "status": "inactive",
    })
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "inactive"


def test_update_chatroom_setting(client):
    resp = client.put("/chatrooms/scid_test-chatroom-001", json={
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
    resp = client.put("/chatrooms/scid_nonexistent", json={"name": "x"})
    assert resp.status_code == 404


# --- DELETE /chatrooms/:id (deactivate) ---

def test_delete_chatroom(client):
    resp = client.delete("/chatrooms/scid_test-chatroom-001")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "deactivated"
    # Should be inactive in store
    assert CHATROOMS["scid_test-chatroom-001"]["status"] == "inactive"


def test_delete_chatroom_not_found(client):
    resp = client.delete("/chatrooms/scid_nonexistent")
    assert resp.status_code == 404


# --- GET /chatrooms/:id/usage ---

def test_get_usage_empty(client):
    resp = client.get("/chatrooms/scid_test-chatroom-001/usage")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["chatroom_id"] == "scid_test-chatroom-001"
    assert data["input_tokens"] == 0
    assert data["output_tokens"] == 0
    assert data["total_tokens"] == 0


def test_get_usage_with_records(client):
    accumulate_usage("scid_test-chatroom-001", 100, 50)
    accumulate_usage("scid_test-chatroom-001", 200, 75)
    # Different chatroom — should not be counted
    accumulate_usage("scid_other", 999, 999)

    resp = client.get("/chatrooms/scid_test-chatroom-001/usage")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["input_tokens"] == 300
    assert data["output_tokens"] == 125
    assert data["total_tokens"] == 425


def test_get_usage_with_date_filter(client):
    # Manually insert records with known timestamps
    USAGE.append({
        "chatroom_id": "scid_test-chatroom-001",
        "input_tokens": 100,
        "output_tokens": 50,
        "created_at": "2025-01-01T00:00:00+00:00",
    })
    USAGE.append({
        "chatroom_id": "scid_test-chatroom-001",
        "input_tokens": 200,
        "output_tokens": 75,
        "created_at": "2025-06-15T00:00:00+00:00",
    })

    # Filter: only records from 2025-03-01 onward
    resp = client.get(
        "/chatrooms/scid_test-chatroom-001/usage?from=2025-03-01T00:00:00"
    )
    data = resp.get_json()
    assert data["input_tokens"] == 200
    assert data["output_tokens"] == 75
    assert data["total_tokens"] == 275


# --- CORS ---

def test_cors_headers(client):
    resp = client.options("/chatrooms", headers={"Origin": "http://localhost:3000"})
    assert "access-control-allow-origin" in {k.lower() for k in resp.headers.keys()}
