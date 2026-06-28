"""Bedrock Converse API wrapper with retry and error classification."""

from __future__ import annotations

import logging
import time
from typing import Callable

import boto3
from botocore.exceptions import ClientError

from chatroom_api import config
from chatroom_api.prompts.speech_scaffold import SPEAK_TOOL_CONFIG, parse_speak_tool_call

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


def _call_with_retry(call: Callable[[], dict]) -> dict:
    """Invoke ``call`` with the shared Bedrock retry + error classification.

    ``call`` is a zero-arg closure that issues a Bedrock API request and
    returns the raw response dict. Retryable ``ClientError`` codes
    (Throttling/ModelTimeout/ServiceUnavailable) trigger exponential backoff
    up to ``MAX_RETRIES``; fatal codes (ExpiredToken/Validation) raise
    immediately. Any other exception is wrapped as a non-retryable
    ``BedrockInferenceError``.
    """
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            return call()
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

        except BedrockInferenceError:
            raise

        except Exception as e:
            raise BedrockInferenceError("UnknownError", str(e), retryable=False)

    # All retries exhausted
    raise last_error or BedrockInferenceError("UnknownError", "max retries exceeded", retryable=True)


def _normalize_system_blocks(system_prompt: str | list[dict]) -> list[dict]:
    """Return Bedrock Converse system blocks.

    Existing callers can still pass a plain string. Cache-aware callers can
    pass an explicit content-block list including ``cachePoint`` blocks.
    """
    if isinstance(system_prompt, str):
        return [{"text": system_prompt}]
    return system_prompt


def invoke(
    model_id: str,
    system_prompt: str | list[dict],
    messages: list[dict],
    *,
    temperature: float = 0.7,
) -> dict:
    """Call Bedrock Converse API with retry for transient errors.

    Returns: {"text": str, "input_tokens": int, "output_tokens": int}
    Raises: BedrockInferenceError on failure.
    """
    client = _get_client()

    def _do_call() -> dict:
        response = client.converse(
            modelId=model_id,
            messages=messages,
            system=_normalize_system_blocks(system_prompt),
            inferenceConfig={"maxTokens": 512, "temperature": temperature},
        )
        return {
            "text": response["output"]["message"]["content"][0]["text"],
            "input_tokens": response["usage"]["inputTokens"],
            "output_tokens": response["usage"]["outputTokens"],
            "cache_read_input_tokens": response["usage"].get("cacheReadInputTokens", 0),
            "cache_write_input_tokens": response["usage"].get("cacheWriteInputTokens", 0),
        }

    return _call_with_retry(_do_call)


def invoke_speak_tool(
    model_id: str,
    system_prompt: str | list[dict],
    messages: list[dict],
    *,
    temperature: float = 0.7,
) -> dict:
    """Call Bedrock Converse API forcing the `speak` tool.

    Wraps the existing retry/error classification.

    Returns:
        {
            "messages": list[str],   # parsed via parse_speak_tool_call; [] if silent
            "input_tokens": int,
            "output_tokens": int,
            "cache_read_input_tokens": int,
            "cache_write_input_tokens": int,
            "raw_response": dict,    # the full Bedrock response, for audit
        }

    Raises: BedrockInferenceError on failure.
    """
    client = _get_client()

    def _do_call() -> dict:
        response = client.converse(
            modelId=model_id,
            messages=messages,
            system=_normalize_system_blocks(system_prompt),
            toolConfig=SPEAK_TOOL_CONFIG,
            inferenceConfig={"maxTokens": 512, "temperature": temperature},
        )
        return {
            "messages": parse_speak_tool_call(response),
            "input_tokens": response["usage"]["inputTokens"],
            "output_tokens": response["usage"]["outputTokens"],
            "cache_read_input_tokens": response["usage"].get("cacheReadInputTokens", 0),
            "cache_write_input_tokens": response["usage"].get("cacheWriteInputTokens", 0),
            "raw_response": response,
        }

    return _call_with_retry(_do_call)
