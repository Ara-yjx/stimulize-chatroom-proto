"""Strict text protocol for models without reliable native tool calls."""

import json

from chatroom_api.prompts.speech_scaffold import SpeechOutputError


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SpeechOutputError('duplicate_json_key')
        result[key] = value
    return result


def parse_json_speech(response: dict) -> list[str]:
    """Parse an explicitly selected JSON response, never a tool-call fallback.

    Missing or malformed output previously masqueraded as intentional silence.
    Do not repair incomplete JSON: valid text may end mid-sentence, but the
    surrounding envelope must still be valid before it becomes chat history.
    """
    try:
        blocks = response['output']['message']['content']
    except (KeyError, TypeError):
        raise SpeechOutputError('missing_content') from None
    if not isinstance(blocks, list):
        raise SpeechOutputError('invalid_content')
    parts = []
    for block in blocks:
        if not isinstance(block, dict):
            raise SpeechOutputError('invalid_content')
        if 'toolUse' in block:
            raise SpeechOutputError('unexpected_tool')
        if 'text' in block:
            if not isinstance(block['text'], str):
                raise SpeechOutputError('invalid_content')
            parts.append(block['text'])
    text = ''.join(parts).strip()
    if not text:
        raise SpeechOutputError('missing_json_text')
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0] in ('```', '```json') and lines[-1] == '```':
        text = '\n'.join(lines[1:-1])
    try:
        payload = json.loads(text, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, SpeechOutputError):
            raise
        raise SpeechOutputError('invalid_json') from None
    if not isinstance(payload, dict) or set(payload) != {'messages'}:
        raise SpeechOutputError('invalid_json_arguments')
    messages = payload['messages']
    if not isinstance(messages, list) or not all(
        isinstance(message, str) and message.strip() for message in messages
    ):
        raise SpeechOutputError('invalid_messages')
    return messages
