import os

DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "chatroom-conversations")
DYNAMODB_EVENT_TABLE = os.environ.get(
    "DYNAMODB_EVENT_TABLE", "chatroom-conversation-events"
)
EVENT_STORAGE_ENABLED = os.environ.get("EVENT_STORAGE_ENABLED", "false").lower() == "true"
CHATROOM_SERVICE_MODE = os.environ.get("CHATROOM_SERVICE_MODE", "normal").lower()
if CHATROOM_SERVICE_MODE not in {"normal", "drain", "maintenance"}:
    raise RuntimeError(
        "CHATROOM_SERVICE_MODE must be normal, drain, or maintenance"
    )
LOBBY_TABLE = os.environ.get("LOBBY_TABLE", "chatroom-lobbies")
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_SECRET_ARN = os.environ.get("JWT_SECRET_ARN", "")
# Retained for existing deployments; durable tick-event reads are removed by
# the event-storage cutover.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
USE_MOCK_DYNAMO = os.environ.get("USE_MOCK_DYNAMO", "true").lower() == "true"
USE_MOCK_RDS = os.environ.get("USE_MOCK_RDS", "true").lower() == "true"
USE_MOCK_LOBBY = os.environ.get("USE_MOCK_LOBBY", "true").lower() == "true"
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-2")
TICK_HANDLER_LAMBDA = os.environ.get("TICK_HANDLER_LAMBDA", "")
AI_BATCH_ENABLED = os.environ.get("AI_BATCH_ENABLED", "false").lower() == "true"
PROMPT_ATTACHMENTS_ENABLED = os.environ.get("PROMPT_ATTACHMENTS_ENABLED", "false").lower() == "true"
PROMPT_ATTACHMENT_BUCKET = os.environ.get("PROMPT_ATTACHMENT_BUCKET", "")
PROMPT_ATTACHMENT_MODELS = frozenset(x.strip() for x in os.environ.get("PROMPT_ATTACHMENT_MODELS", "").split(",") if x.strip())
AI_BATCH_TABLE = os.environ.get("AI_BATCH_TABLE", "ai-conversation-batches")
AI_BATCH_PROVISION_QUEUE_URL = os.environ.get("AI_BATCH_PROVISION_QUEUE_URL", "")
AI_BATCH_STATE_MACHINE_ARN = os.environ.get("AI_BATCH_STATE_MACHINE_ARN", "")
AI_BATCH_EXPORT_QUEUE_URL = os.environ.get("AI_BATCH_EXPORT_QUEUE_URL", "")
AI_BATCH_EXPORT_BUCKET = os.environ.get("AI_BATCH_EXPORT_BUCKET", "")

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

# Stimulize EC2 prepaid-credit APIs. Empty URL skips the check so local
# mock/dev ticks still run. Token falls back to MGMT_API_TOKEN for existing
# Lambda env.
STIMULIZE_API_URL = os.environ.get("STIMULIZE_API_URL", "")
STIMULIZE_API_TOKEN = os.environ.get("STIMULIZE_API_TOKEN", "") or os.environ.get("MGMT_API_TOKEN", "")
