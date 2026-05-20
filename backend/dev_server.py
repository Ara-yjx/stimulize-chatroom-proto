"""Local dev server — wraps the Lambda handler behind a Flask HTTP server.

Also spawns the heartbeat as a daemon thread when ``TICK_HANDLER_LOCAL`` is
truthy (default ``true`` for local dev). The thread queries active
conversations the same way the production heartbeat container does, but
calls ``tick_handler.handle_tick`` in-process instead of async-invoking a
Lambda. See ``docs/low-level-design.md`` "Local dev" section.
"""

import logging as _logging
import os
import threading
import time as _time

os.environ.setdefault("USE_MOCK_DYNAMO", "true")
os.environ.setdefault("USE_MOCK_RDS", "true")
os.environ.setdefault("USE_MOCK_LOBBY", "true")
os.environ.setdefault("JWT_SECRET", "dev-secret")
os.environ.setdefault("ADMIN_TOKEN", "dev-admin-token")
os.environ.setdefault("BEDROCK_REGION", "us-east-2")
os.environ.setdefault("TICK_HANDLER_LOCAL", "true")
os.environ.setdefault("HEARTBEAT_INTERVAL_SEC", "5")

# Dump every conversation row to disk on each mutation so the operator can
# inspect full audit (events, ticks, participants) live with `jq` or a tail.
# Disabled at the env-var level when not in dev (see mock_dynamo._maybe_dump).
_DEFAULT_DUMP_DIR = os.path.join(os.path.dirname(__file__), "dev_dumps", "conversations")
os.environ.setdefault("DEV_DUMP_CONVERSATIONS_DIR", _DEFAULT_DUMP_DIR)

from flask import Flask, send_from_directory, request
from chatroom_api.handler import lambda_handler

app = Flask(__name__)
# CORS headers come from the Lambda handler (chatroom_api.handler._CORS_HEADERS)
# so behavior matches prod (API Gateway returns whatever the Lambda sets).
# Don't add flask-cors here — it'd duplicate Access-Control-Allow-Origin and
# the browser rejects responses with two values.

# Serve the frontend widget bundle — check frontend/ first, fall back to frontend_legacy/
_FRONTEND_DIRS = [
    os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"),
    os.path.join(os.path.dirname(__file__), "..", "frontend_legacy", "dist"),
]


@app.route("/chatroom.min.js")
def serve_widget():
    for dist_dir in _FRONTEND_DIRS:
        js_path = os.path.join(dist_dir, "chatroom.min.js")
        if os.path.isfile(js_path):
            return send_from_directory(dist_dir, "chatroom.min.js", mimetype="application/javascript")
    return "chatroom.min.js not found", 404


@app.route("/<path:path>", methods=["GET", "POST", "OPTIONS"])
@app.route("/", methods=["GET", "POST", "OPTIONS"])
def proxy(path=""):
    event = {
        "httpMethod": request.method,
        "path": "/" + path,
        "headers": dict(request.headers),
        "body": request.get_data(as_text=True) or None,
        "queryStringParameters": dict(request.args) if request.args else None,
    }
    result = lambda_handler(event, None)
    return (
        result["body"],
        result["statusCode"],
        result["headers"],
    )


def _maybe_start_local_heartbeat():
    """Spawn a daemon thread that ticks every active conversation in-process.

    Only runs when ``TICK_HANDLER_LOCAL=true`` (default for local dev). In
    production the heartbeat container handles this.
    """
    if os.environ.get("TICK_HANDLER_LOCAL", "true").lower() != "true":
        return

    interval = int(os.environ.get("HEARTBEAT_INTERVAL_SEC", "5"))
    log = _logging.getLogger("dev.heartbeat")

    def _list_active_conversation_ids() -> list[str]:
        # Lazy import so we don't pay the cost when the heartbeat is disabled.
        from chatroom_api import config

        if config.USE_MOCK_DYNAMO:
            from chatroom_api import mock_dynamo

            return [
                cid
                for cid, room in mock_dynamo._rooms.items()
                if room.get("status") == "active"
            ]

        from chatroom_api import dynamo

        table = dynamo._get_table()
        resp = table.query(
            IndexName="status-index",
            KeyConditionExpression="#s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "active"},
        )
        return [item["conversation_id"] for item in resp.get("Items", [])]

    def _loop():
        from chatroom_api.tick_handler import handle_tick

        while True:
            try:
                cids = _list_active_conversation_ids()
                for cid in cids:
                    try:
                        handle_tick({"conversation_id": cid}, None)
                    except Exception as exc:
                        log.warning("local heartbeat: tick %s failed: %s", cid, exc)
            except Exception as exc:
                log.warning("local heartbeat loop error: %s", exc)
            _time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="local-heartbeat")
    t.start()
    log.info("local heartbeat started (interval=%ds)", interval)


# Call at module load so both ``python dev_server.py`` and a WSGI runner
# (which imports this module) get the heartbeat thread.
_maybe_start_local_heartbeat()


if __name__ == "__main__":
    # Disable Flask's reloader: it spawns a child process which would start a
    # second heartbeat thread and double up tick invocations.
    app.run(port=5001, debug=True, use_reloader=False)
