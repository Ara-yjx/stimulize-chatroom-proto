"""Explicit AI-only speech/consent protocol; never a human-chat fallback."""

from chatroom_api.prompts.json_speech import parse_json_payload
from chatroom_api.prompts.speech_scaffold import SpeechOutputError, build_speak_tool_config


def build_ai_action_tool_config(*, require_message: bool) -> dict:
    """Require one tool while permitting consent instead of forced speech."""
    config = build_speak_tool_config(require_message=require_message, max_messages=1)
    config['tools'].append({'toolSpec': {
        'name': 'agreeToEnd',
        'description': (
            'Confirm that you reviewed the latest conversation and have no further '
            'substantive contribution. This is willingness to end, not agreement '
            'with every opinion. Send no message with this action.'
        ),
        'inputSchema': {'json': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    }})
    config['toolChoice'] = {'any': {}}
    return config


def parse_ai_action(response: dict, *, json_protocol: bool) -> tuple[str, list[str]]:
    """Accept exactly one complete action; malformed output is not consent.

    Text models use an explicit action object, native models use a named tool.
    Keep this separate from human-AI parsing, including its historic tolerance
    for tool envelopes. Truncated *speech* stays usable, but a control decision
    cannot be inferred from a provider response that ended at its token budget.
    """
    if json_protocol:
        payload = parse_json_payload(response)
        agreement = payload == {'action': 'agree_to_end'}
    else:
        blocks = (response.get('output') or {}).get('message', {}).get('content')
        if not isinstance(blocks, list) or not all(isinstance(b, dict) for b in blocks):
            raise SpeechOutputError('invalid_content')
        tools = [b['toolUse'] for b in blocks if 'toolUse' in b]
        if not tools:
            raise SpeechOutputError('missing_tool_call')
        if len(tools) != 1:
            raise SpeechOutputError('multiple_tool_calls')
        tool = tools[0]
        if not isinstance(tool, dict) or tool.get('name') not in {'speak', 'agreeToEnd'}:
            raise SpeechOutputError('unexpected_tool')
        payload = tool.get('input')
        agreement = tool['name'] == 'agreeToEnd'
        if agreement and payload != {}:
            raise SpeechOutputError('invalid_agreement_arguments')
    if agreement:
        if response.get('stopReason') == 'max_tokens':
            raise SpeechOutputError('truncated_control_output')
        return 'agree_to_end', []
    if not isinstance(payload, dict) or set(payload) != {'messages'}:
        raise SpeechOutputError('invalid_action_arguments')
    messages = payload['messages']
    if not isinstance(messages, list) or not all(isinstance(m, str) and m.strip() for m in messages):
        raise SpeechOutputError('invalid_messages')
    return ('speech' if messages else 'silence'), messages
