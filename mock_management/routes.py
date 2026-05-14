# Chatroom CRUD + usage endpoints for the mock management API.

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from chatroom_config import CHATROOMS, USAGE, accumulate_usage, generate_chatroom_id

# ---------------------------------------------------------------------------
# Admin routes (called by editor UI)
# ---------------------------------------------------------------------------

admin_bp = Blueprint("admin", __name__)


# --- Chatroom CRUD ---


@admin_bp.route("/chatrooms", methods=["POST"])
def create_chatroom():
    """Create a new chatroom. Generates scid_ + uuid4 as the ID."""
    body = request.get_json(force=True)
    now = datetime.now(timezone.utc).isoformat()
    chatroom_id = generate_chatroom_id()

    chatroom = {
        "id": chatroom_id,
        "owner_id": "owner_default",
        "name": body["name"],
        "status": "active",
        "setting": body["setting"],
        "created_at": now,
        "updated_at": now,
    }
    CHATROOMS[chatroom_id] = chatroom
    return jsonify(chatroom), 201


@admin_bp.route("/chatrooms", methods=["GET"])
def list_chatrooms():
    """List all chatrooms (summary: id, name, status, created_at, updated_at)."""
    summaries = [
        {
            "id": c["id"],
            "name": c["name"],
            "status": c["status"],
            "created_at": c["created_at"],
            "updated_at": c["updated_at"],
        }
        for c in CHATROOMS.values()
    ]
    return jsonify(summaries)


@admin_bp.route("/chatrooms/<chatroom_id>", methods=["GET"])
def get_chatroom(chatroom_id):
    """Get a single chatroom with full setting."""
    chatroom = CHATROOMS.get(chatroom_id)
    if chatroom is None:
        return jsonify({"error": "chatroom not found"}), 404
    return jsonify(chatroom)


@admin_bp.route("/chatrooms/<chatroom_id>", methods=["PUT"])
def update_chatroom(chatroom_id):
    """Partial update: name, status, setting."""
    chatroom = CHATROOMS.get(chatroom_id)
    if chatroom is None:
        return jsonify({"error": "chatroom not found"}), 404

    body = request.get_json(force=True)
    now = datetime.now(timezone.utc).isoformat()

    if "name" in body:
        chatroom["name"] = body["name"]
    if "status" in body:
        chatroom["status"] = body["status"]
    if "setting" in body:
        chatroom["setting"] = body["setting"]
    chatroom["updated_at"] = now

    return jsonify(chatroom)


@admin_bp.route("/chatrooms/<chatroom_id>", methods=["DELETE"])
def delete_chatroom(chatroom_id):
    """Deactivate a chatroom (set status to inactive)."""
    chatroom = CHATROOMS.get(chatroom_id)
    if chatroom is None:
        return jsonify({"error": "chatroom not found"}), 404

    now = datetime.now(timezone.utc).isoformat()
    chatroom["status"] = "inactive"
    chatroom["updated_at"] = now
    return jsonify({"status": "deactivated"})


# --- Usage ---


@admin_bp.route("/chatrooms/<chatroom_id>/usage", methods=["GET"])
def get_chatroom_usage(chatroom_id):
    """Get token usage totals for a chatroom, optionally filtered by date range."""
    from_date = request.args.get("from")
    to_date = request.args.get("to")

    input_tokens = 0
    output_tokens = 0

    for record in USAGE:
        if record["chatroom_id"] != chatroom_id:
            continue
        if from_date and record["created_at"] < from_date:
            continue
        if to_date and record["created_at"] > to_date:
            continue
        input_tokens += record["input_tokens"]
        output_tokens += record["output_tokens"]

    return jsonify({
        "chatroom_id": chatroom_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    })


# ---------------------------------------------------------------------------
# Legacy key validation routes (commented out for reference)
# The internal blueprint is no longer needed — Lambda reads RDS directly.
# ---------------------------------------------------------------------------

# import secrets
# from chatroom_config import KEYS, KEY_LOOKUP
#
# internal_bp = Blueprint("internal", __name__, url_prefix="/internal")
#
# @internal_bp.route("/keys/<chatroom_key>", methods=["GET"])
# def get_key(chatroom_key):
#     key_id = KEY_LOOKUP.get(chatroom_key)
#     if key_id is None:
#         return jsonify({"error": "key not found"}), 404
#     entry = KEYS[key_id]
#     return jsonify({
#         "channel_id": entry["channel_id"],
#         "is_active": entry["is_active"],
#         "restrictions": entry["restrictions"],
#     })
#
# @internal_bp.route("/usage", methods=["POST"])
# def post_usage():
#     body = request.get_json(force=True)
#     accumulate_usage(
#         chatroom_id=body["chatroom_id"],
#         input_tokens=body["input_tokens"],
#         output_tokens=body["output_tokens"],
#     )
#     return jsonify({"status": "ok"}), 200
#
# Legacy admin key routes:
# @admin_bp.route("/keys", methods=["POST"])
# def create_key(): ...
# @admin_bp.route("/keys", methods=["GET"])
# def list_keys(): ...
# @admin_bp.route("/keys/<key_id>", methods=["PUT"])
# def update_key(key_id): ...
# @admin_bp.route("/keys/<key_id>", methods=["DELETE"])
# def delete_key(key_id): ...


def register_routes(app):
    """Register all route blueprints with the Flask app."""
    app.register_blueprint(admin_bp)
