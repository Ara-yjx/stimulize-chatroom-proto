# Chatroom CRUD + usage endpoints for the mock management API.

from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from chatroom_config import CHATROOMS, USAGE, accumulate_usage, generate_chatroom_id

# ---------------------------------------------------------------------------
# Admin routes (called by editor UI)
# ---------------------------------------------------------------------------

admin_bp = Blueprint("admin", __name__)


# --- Chatroom CRUD ---


@admin_bp.route("/api/createChatroom", methods=["POST"])
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


@admin_bp.route("/api/getChatrooms", methods=["POST"])
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


@admin_bp.route("/api/getChatroom/<chatroom_id>", methods=["POST"])
def get_chatroom(chatroom_id):
    """Get a single chatroom with full setting."""
    chatroom = CHATROOMS.get(chatroom_id)
    if chatroom is None:
        return jsonify({"error": "chatroom not found"}), 404
    return jsonify(chatroom)


@admin_bp.route("/api/updateChatroom/<chatroom_id>", methods=["POST"])
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


@admin_bp.route("/api/deleteChatroom/<chatroom_id>", methods=["POST"])
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

VALID_USAGE_PERIODS = {"hour", "day", "week", "month"}


def _parse_usage_period(value):
    if value in (None, ""):
        return None
    if value not in VALID_USAGE_PERIODS:
        raise ValueError("period must be one of hour, day, week, month")
    return value


def _parse_iso_datetime(value, field_name):
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO datetime string") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bucket_start(dt: datetime, period: str) -> datetime:
    if period == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    if period == "day":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return day_start - timedelta(days=day_start.weekday())
    if period == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError("unsupported period")


def _aggregate_usage(records, period):
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.0,
    }
    series_map = {}

    for record in records:
        totals["input_tokens"] += int(record["input_tokens"])
        totals["output_tokens"] += int(record["output_tokens"])
        totals["estimated_cost_usd"] += float(record.get("estimated_cost_usd", 0.0))

        if not period:
            continue
        invoked_at = _parse_iso_datetime(record["invoked_at"], "invoked_at")
        bucket = _bucket_start(invoked_at, period).isoformat()
        bucket_totals = series_map.setdefault(bucket, {
            "period_start": bucket,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
        })
        bucket_totals["input_tokens"] += int(record["input_tokens"])
        bucket_totals["output_tokens"] += int(record["output_tokens"])
        bucket_totals["estimated_cost_usd"] += float(record.get("estimated_cost_usd", 0.0))

    totals["estimated_cost_usd"] = round(totals["estimated_cost_usd"], 8)
    series = []
    for bucket in sorted(series_map):
        item = series_map[bucket]
        item["estimated_cost_usd"] = round(item["estimated_cost_usd"], 8)
        series.append(item)
    return totals, series


@admin_bp.route("/api/getChatroomUsage/<chatroom_id>", methods=["POST"])
def get_chatroom_usage(chatroom_id):
    """Get usage totals for a chatroom, with optional time filters and series."""
    body = request.get_json(silent=True) or {}
    try:
        period = _parse_usage_period(body.get("period"))
        from_date = _parse_iso_datetime(body.get("from"), "from")
        to_date = _parse_iso_datetime(body.get("to"), "to")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if from_date and to_date and from_date > to_date:
        return jsonify({"error": "from must be earlier than or equal to to"}), 400

    filtered = []
    for record in USAGE:
        if record["chatroom_id"] != chatroom_id:
            continue
        invoked_at = _parse_iso_datetime(record["invoked_at"], "invoked_at")
        if from_date and invoked_at < from_date:
            continue
        if to_date and invoked_at > to_date:
            continue
        filtered.append(record)

    totals, series = _aggregate_usage(filtered, period)
    return jsonify({
        "scope": "chatroom",
        "chatroom_id": chatroom_id,
        "period": period,
        "from": from_date.isoformat() if from_date else None,
        "to": to_date.isoformat() if to_date else None,
        "totals": totals,
        "series": series,
    })


@admin_bp.route("/api/getUserUsage", methods=["POST"])
def get_user_usage():
    body = request.get_json(silent=True) or {}
    owner_id = body.get("owner_id", "owner_default")
    try:
        period = _parse_usage_period(body.get("period"))
        from_date = _parse_iso_datetime(body.get("from"), "from")
        to_date = _parse_iso_datetime(body.get("to"), "to")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if from_date and to_date and from_date > to_date:
        return jsonify({"error": "from must be earlier than or equal to to"}), 400

    filtered = []
    for record in USAGE:
        if record["owner_id"] != owner_id:
            continue
        invoked_at = _parse_iso_datetime(record["invoked_at"], "invoked_at")
        if from_date and invoked_at < from_date:
            continue
        if to_date and invoked_at > to_date:
            continue
        filtered.append(record)

    totals, series = _aggregate_usage(filtered, period)
    return jsonify({
        "scope": "user",
        "owner_id": owner_id,
        "period": period,
        "from": from_date.isoformat() if from_date else None,
        "to": to_date.isoformat() if to_date else None,
        "totals": totals,
        "series": series,
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
