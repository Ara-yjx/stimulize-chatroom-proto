import os

DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "chatroom-conversations")
LOBBY_TABLE = os.environ.get("LOBBY_TABLE", "chatroom-lobbies")
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_SECRET_ARN = os.environ.get("JWT_SECRET_ARN", "")
# Admin bearer for /chat/messages?include_ticks=true. Empty by default so
# admin features are disabled until the secret is explicitly set.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
USE_MOCK_DYNAMO = os.environ.get("USE_MOCK_DYNAMO", "true").lower() == "true"
USE_MOCK_RDS = os.environ.get("USE_MOCK_RDS", "true").lower() == "true"
USE_MOCK_LOBBY = os.environ.get("USE_MOCK_LOBBY", "true").lower() == "true"
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-2")

# RDS connection params (used by rds.py when USE_MOCK_RDS is False)
RDS_HOST = os.environ.get("RDS_HOST", "")
RDS_PORT = int(os.environ.get("RDS_PORT", "5432"))
RDS_DATABASE = os.environ.get("RDS_DATABASE", "")
RDS_USERNAME = os.environ.get("RDS_USERNAME", "")
RDS_PASSWORD = os.environ.get("RDS_PASSWORD", "")
RDS_SECRET_ARN = os.environ.get("RDS_SECRET_ARN", "")

# Management API fallback for chatroom reads only. Direct Postgres is the
# primary architecture for both chatroom reads and usage writes. This HTTP
# path exists only for legacy environments where RDS access is intentionally
# unavailable. ``MGMT_API_TOKEN`` is the bearer the management API expects.
MGMT_API_URL = os.environ.get("MGMT_API_URL", "")
MGMT_API_TOKEN = os.environ.get("MGMT_API_TOKEN", "")
