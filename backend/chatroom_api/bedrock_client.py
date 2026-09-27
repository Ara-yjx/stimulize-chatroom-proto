"""Bedrock Converse API wrapper with retry and error classification."""

from __future__ import annotations

import logging
import time
from typing import Callable

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError

from chatroom_api import config
from chatroom_api.prompts.construction import base_bedrock_model_id
from chatroom_api.prompts.speech_protocol import uses_json_speech
from chatroom_api.prompts.json_speech import parse_json_speech
from chatroom_api.prompts.speech_scaffold import (
    REQUIRED_SPEAK_TOOL_CONFIG,
    SPEAK_TOOL_CONFIG,
    build_speak_tool_config,
    parse_speak_tool_call,
    SpeechOutputError,
)

logger = logging.getLogger(__name__)

_client = None
_deadline_client = None

# Errors that are transient and worth retrying
_RETRYABLE_ERRORS = {"ThrottlingException", "ModelTimeoutException", "ServiceUnavailableException"}

# Errors that are fatal — no point retrying
_FATAL_ERRORS = {"ExpiredTokenException", "ValidationException"}

# Re-export application-level errors for convenience
from chatroom_api.errors import ChatroomNotFoundException, InactiveChatroomException  # noqa: E402, F401

MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds
# The first short-chat implementation hard-coded 512 and the batch path
# inherited it. This is an inference safety budget, not a message-length rule.
SPEAK_MAX_OUTPUT_TOKENS = 2048

# These Converse routes reject forced named-tool selection but passed our
# repeated auto-tool probes. Keep this explicit: Llama 3.1 did not pass.
AUTO_SPEAK_MODEL_IDS = frozenset({
    'meta.llama3-3-70b-instruct-v1:0',
    'meta.llama4-scout-17b-instruct-v1:0',
    'meta.llama4-maverick-17b-instruct-v1:0',
})


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


def _get_deadline_client():
    global _deadline_client
    if _deadline_client is None:
        # Explicit retries must pass the caller's deadline check. Disable SDK
        # retries underneath it; keep ordinary interactive callers unchanged.
        _deadline_client = boto3.client(
            "bedrock-runtime", region_name=config.BEDROCK_REGION,
            config=BotoConfig(connect_timeout=5, read_timeout=90, retries={"total_max_attempts": 1}),
        )
    return _deadline_client


def _bounded_converse(client, **request):
    # Bedrock Opus 4.7 rejects sampling-temperature overrides.
    if 'claude-opus-4-7' in request.get('modelId', ''):
        request['inferenceConfig'] = {k: v for k, v in request.get('inferenceConfig', {}).items() if k != 'temperature'}
    if config.PROMPT_ATTACHMENTS_ENABLED:
        from chatroom_api.prompt_attachments import check_serialized_request
        check_serialized_request(request, client)
    return client.converse(**request)


def _call_with_retry(call: Callable[[], dict], before_attempt: Callable[[], None] | None = None) -> dict:
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
        if before_attempt:
            before_attempt()
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

        except (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError) as e:
            raise BedrockInferenceError(type(e).__name__, str(e), retryable=before_attempt is not None)

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
        response = _bounded_converse(client,
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


def _speak_result(response: dict, *, require_message: bool, max_messages: int, json_protocol: bool = False) -> dict:
    stop_reason = response.get('stopReason')
    truncated = stop_reason == 'max_tokens'
    error = None
    messages = []
    try:
        if stop_reason in {'guardrail_intervened', 'content_filtered', 'malformed_model_output',
                           'malformed_tool_use', 'model_context_window_exceeded'}:
            raise SpeechOutputError(stop_reason)
        messages = parse_json_speech(response) if json_protocol else parse_speak_tool_call(response)
        if len(messages) > max_messages:
            raise SpeechOutputError('too_many_messages')
        if not messages and (truncated or require_message):
            raise SpeechOutputError('truncated_without_message' if truncated else 'required_speech_missing')
    except SpeechOutputError as exc:
        error = 'truncated_without_message' if truncated and not messages else str(exc)
        messages = []
    usage = response.get('usage') or {}
    # Chat text may end mid-sentence, like a messaging app's length limit:
    # usable truncated messages are successful speech, never retried for polish.
    # An empty/undecodable tool input has no text to display; do not fabricate
    # speech or call it intentional silence. Return errors WITH usage so callers
    # can account for this invocation before retrying or recording a failure.
    return {
        'messages': messages,
        'outcome': 'error' if error else 'speech' if messages else 'silence',
        'output_error': error,
        'truncated': truncated,
        'stop_reason': stop_reason,
        'request_id': (response.get('ResponseMetadata') or {}).get('RequestId'),
        'input_tokens': usage.get('inputTokens', 0),
        'output_tokens': usage.get('outputTokens', 0),
        'cache_read_input_tokens': usage.get('cacheReadInputTokens', 0),
        'cache_write_input_tokens': usage.get('cacheWriteInputTokens', 0),
        'raw_response': response,
    }


def log_speak_result(result: dict, model_id: str, *, conversation_id: str,
                     ai_participant_id: str, recovery_attempt: int = 0) -> None:
    if not result.get('output_error') and not result.get('truncated'):
        return
    # Specific diagnostics without research prompts, history or model text.
    logger.warning(
        'Bedrock output: error=%s truncated=%s model=%s conversation=%s ai=%s '
        'request_id=%s stop_reason=%s input_tokens=%s output_tokens=%s recovery_attempt=%s',
        result.get('output_error') or 'none', bool(result.get('truncated')), model_id,
        conversation_id, ai_participant_id, result.get('request_id'), result.get('stop_reason'),
        result.get('input_tokens'), result.get('output_tokens'), recovery_attempt,
    )


def invoke_speak_tool(
    model_id: str,
    system_prompt: str | list[dict],
    messages: list[dict],
    *,
    temperature: float = 0.7,
    require_message: bool = False,
    max_message_chars: int | None = None,
    max_messages: int = 5,
    before_attempt: Callable[[], None] | None = None,
) -> dict:
    """Call Bedrock Converse API with the model's supported tool choice.

    Wraps the existing retry/error classification.

    Returns:
        {
            "messages": list[str],   # consult output_error before treating [] as silence
            "output_error": str | None,
            "truncated": bool,
            "input_tokens": int,
            "output_tokens": int,
            "cache_read_input_tokens": int,
            "cache_write_input_tokens": int,
            "raw_response": dict,    # the full Bedrock response, for audit
        }

    Raises: BedrockInferenceError on failure.
    """
    client = _get_deadline_client() if before_attempt else _get_client()

    tool_config = (
        build_speak_tool_config(
            require_message=require_message,
            max_message_chars=max_message_chars,
            max_messages=max_messages,
        )
        if max_message_chars is not None or max_messages != 5
        else REQUIRED_SPEAK_TOOL_CONFIG if require_message else SPEAK_TOOL_CONFIG
    )
    if base_bedrock_model_id(model_id) in AUTO_SPEAK_MODEL_IDS:
        # Copy instead of mutating the shared constants used by other models.
        tool_config = {**tool_config, 'toolChoice': {'auto': {}}}

    def _do_call() -> dict:
        response = _bounded_converse(client,
            modelId=model_id,
            messages=messages,
            system=_normalize_system_blocks(system_prompt),
            **({} if uses_json_speech(model_id) else {'toolConfig': tool_config}),
            inferenceConfig={"maxTokens": SPEAK_MAX_OUTPUT_TOKENS, "temperature": temperature},
        )
        return _speak_result(response, require_message=require_message, max_messages=max_messages,
                             json_protocol=uses_json_speech(model_id))

    return _call_with_retry(_do_call, before_attempt)
