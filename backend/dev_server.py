"""Local dev server — wraps the Lambda handler behind a Flask HTTP server."""

import json
import os

os.environ.setdefault("USE_MOCK_DYNAMO", "true")
os.environ.setdefault("USE_MOCK_RDS", "true")
os.environ.setdefault("JWT_SECRET", "dev-secret")
os.environ.setdefault("BEDROCK_REGION", "us-east-2")

from flask import Flask, request, send_from_directory
from flask_cors import CORS
from chatroom_api.handler import lambda_handler

app = Flask(__name__)
CORS(app)

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


if __name__ == "__main__":
    app.run(port=5001, debug=True)
