"""Bedrock Converse API wrapper with retry and error classification."""

from __future__ import annotations

import logging
import time

import boto3
from botocore.exceptions import ClientError

from chatroom_api import config

logger = logging.getLogger(__name__)

_client = None

# Errors that are transient and worth retrying
_RETRYABLE_ERRORS = {"ThrottlingException", "ModelTimeoutException", "ServiceUnavailableException"}

# Errors that are fatal — no point retrying
_FATAL_ERRORS = {"ExpiredTokenException", "ValidationException"}

# Re-export application-level errors for convenience
from chatroom_api.errors import ChatroomNotFoundException, InactiveChatroomException  # noqa: E402, F401

MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds


class BedrockInferenceError(Exception):
    """Raised when Bedrock inference fails after retries."""

    def __init__(self, error_type: str, message: str, retryable: bool):
        self.error_type = error_type
        self.message = message
        self.retryable = retryable
        super().__init__(f"[{error_type}] {message}")


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=config.BEDROCK_REGION)
    return _client


def invoke(model_id: str, system_prompt: str, messages: list[dict]) -> dict:
    """Call Bedrock Converse API with retry for transient errors.

    Returns: {"text": str, "input_tokens": int, "output_tokens": int}
    Raises: BedrockInferenceError on failure.
    """
    client = _get_client()
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            response = client.converse(
                modelId=model_id,
                messages=messages,
                system=[{"text": system_prompt}],
                inferenceConfig={"maxTokens": 512, "temperature": 0.7},
            )
            return {
                "text": response["output"]["message"]["content"][0]["text"],
                "input_tokens": response["usage"]["inputTokens"],
                "output_tokens": response["usage"]["outputTokens"],
            }
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_msg = e.response["Error"]["Message"]
            logger.warning("Bedrock error (attempt %d/%d): [%s] %s", attempt + 1, MAX_RETRIES, error_code, error_msg)

            if error_code in _FATAL_ERRORS:
                raise BedrockInferenceError(error_code, error_msg, retryable=False)

            if error_code in _RETRYABLE_ERRORS:
                last_error = BedrockInferenceError(error_code, error_msg, retryable=True)
                if attempt < MAX_RETRIES - 1:
                    delay = BASE_DELAY * (2 ** attempt)
                    time.sleep(delay)
                continue

            # Unknown error — treat as fatal
            raise BedrockInferenceError(error_code, error_msg, retryable=False)

        except Exception as e:
            raise BedrockInferenceError("UnknownError", str(e), retryable=False)

    # All retries exhausted
    raise last_error or BedrockInferenceError("UnknownError", "max retries exceeded", retryable=True)
