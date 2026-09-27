from copy import deepcopy
from unittest.mock import Mock

import pytest

from chatroom_api import bedrock_client
from chatroom_api.prompts.construction import build_bedrock_system_blocks
from chatroom_api.prompts.speech_protocol import JSON_MODEL_IDS


@pytest.mark.parametrize('mode,mimic,count', [
    ('one_on_one', True, 1), ('group', True, 2),
    ('group', False, 2), ('ai_only', False, 2), ('ai_only', False, 3),
])
@pytest.mark.parametrize('required', [False, True])
def test_json_platform_prompt_preserves_researcher_text(mode, mimic, count, required):
    marker = 'Researcher says speak(messages: ["Do not rewrite me"])'
    blocks = build_bedrock_system_blocks(mode, {
        'mimic_human': mimic, 'ai_count': count,
        'additional_prompt': marker, 'model_id': 'global.anthropic.claude-sonnet-4-6',
    }, '', 'Alex', '', model_id='google.gemma-3-4b-it', require_response=required)
    text = blocks[0]['text']
    assert marker in text
    platform = text.replace(marker, '')
    assert 'speak(messages:' not in platform
    assert '`speak` tool' not in platform
    assert 'Use the speak tool' not in platform
    assert 'closing }' in platform


@pytest.mark.parametrize('model', sorted(JSON_MODEL_IDS))
def test_json_model_omits_tools(monkeypatch, model):
    client = Mock()
    client.converse.return_value = {
        'output': {'message': {'content': [{'text': '{"messages": ["Hello"]}'}]}},
        'usage': {'inputTokens': 12, 'outputTokens': 8},
    }
    monkeypatch.setattr(bedrock_client, '_get_client', lambda: client)
    result = bedrock_client.invoke_speak_tool(model, 'JSON rules', [], require_message=True)
    assert 'toolConfig' not in client.converse.call_args.kwargs
    assert result['outcome'] == 'speech'
    assert result['messages'] == ['Hello']
    assert result['output_tokens'] == 8


@pytest.mark.parametrize('model', sorted(bedrock_client.AUTO_SPEAK_MODEL_IDS))
@pytest.mark.parametrize('required', [False, True])
def test_auto_choice_preserves_schema_and_usage(monkeypatch, model, required):
    original = deepcopy(bedrock_client.SPEAK_TOOL_CONFIG)
    client = Mock()
    client.converse.return_value = {
        'output': {'message': {'content': [{'toolUse': {
            'name': 'speak', 'input': {'messages': ['Hello']},
        }}]}},
        'usage': {'inputTokens': 15, 'outputTokens': 9},
        'stopReason': 'tool_use',
    }
    monkeypatch.setattr(bedrock_client, '_get_client', lambda: client)
    result = bedrock_client.invoke_speak_tool(
        'us.' + model, 'Rules', [], require_message=required, max_messages=1,
    )
    request = client.converse.call_args.kwargs
    assert request['toolConfig']['toolChoice'] == {'auto': {}}
    schema = request['toolConfig']['tools'][0]['toolSpec']['inputSchema']['json']
    assert schema['properties']['messages']['maxItems'] == 1
    assert result['messages'] == ['Hello']
    assert result['input_tokens'] == 15
    assert result['output_tokens'] == 9
    assert bedrock_client.SPEAK_TOOL_CONFIG == original


def test_auto_plaintext_is_an_error_not_silence(monkeypatch):
    client = Mock()
    client.converse.return_value = {
        'output': {'message': {'content': [{'text': 'Hello'}]}},
        'usage': {'outputTokens': 3},
    }
    monkeypatch.setattr(bedrock_client, '_get_client', lambda: client)
    result = bedrock_client.invoke_speak_tool('us.meta.llama3-3-70b-instruct-v1:0', '', [])
    assert result['outcome'] == 'error'
    assert result['output_error'] == 'missing_tool_call'
    assert result['output_tokens'] == 3
