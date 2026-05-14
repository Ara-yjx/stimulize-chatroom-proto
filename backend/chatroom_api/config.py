import os

DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "chatroom-conversations")
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_SECRET_ARN = os.environ.get("JWT_SECRET_ARN", "")
MANAGEMENT_API_URL = os.environ.get("MANAGEMENT_API_URL", "http://localhost:5000")
USE_MOCK_DYNAMO = os.environ.get("USE_MOCK_DYNAMO", "true").lower() == "true"
USE_MOCK_RDS = os.environ.get("USE_MOCK_RDS", "true").lower() == "true"
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-2")

# RDS connection params (used by rds.py when USE_MOCK_RDS is False)
RDS_HOST = os.environ.get("RDS_HOST", "")
RDS_PORT = int(os.environ.get("RDS_PORT", "5432"))
RDS_DATABASE = os.environ.get("RDS_DATABASE", "")
RDS_USERNAME = os.environ.get("RDS_USERNAME", "")
RDS_PASSWORD = os.environ.get("RDS_PASSWORD", "")
