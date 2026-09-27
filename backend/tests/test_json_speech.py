import pytest

from chatroom_api.prompts.json_speech import parse_json_speech
from chatroom_api.prompts.speech_scaffold import SpeechOutputError


def response(text):
    return {'output': {'message': {'content': [{'text': text}]}}}


@pytest.mark.parametrize('text,expected', [
    ('{"messages": []}', []),
    ('{"messages": ["Hello", "A thought ends mid-"]}', ['Hello', 'A thought ends mid-']),
    ('```json\n{"messages": ["Hello"]}\n```', ['Hello']),
    ('```\n{"messages": []}\n```', []),
])
def test_valid(text, expected):
    assert parse_json_speech(response(text)) == expected


@pytest.mark.parametrize('text,error', [
    ('', 'missing_json_text'),
    ('Hello', 'invalid_json'),
    ('speak(messages: ["Hello"])', 'invalid_json'),
    ('{"messages": ["Hello"]', 'invalid_json'),
    ('{"messages": [], "messages": ["Hello"]}', 'duplicate_json_key'),
    ('{"messages": [], "extra": true}', 'invalid_json_arguments'),
    ('{}', 'invalid_json_arguments'),
    ('[]', 'invalid_json_arguments'),
    ('{"messages": "Hello"}', 'invalid_messages'),
    ('{"messages": [null]}', 'invalid_messages'),
    ('{"messages": [" "]}', 'invalid_messages'),
    ('{"messages": [NaN]}', 'invalid_messages'),
])
def test_invalid_is_not_silence(text, error):
    with pytest.raises(SpeechOutputError, match=f'^{error}$'):
        parse_json_speech(response(text))


def test_reasoning_is_not_chat_text():
    assert parse_json_speech({'output': {'message': {'content': [
        {'reasoningContent': {'reasoningText': {'text': 'Private reasoning'}}},
        {'text': '{"messages":'}, {'text': ' ["Hello"]}'},
    ]}}}) == ['Hello']


def test_tool_output_is_not_json_protocol():
    with pytest.raises(SpeechOutputError, match='unexpected_tool'):
        parse_json_speech({'output': {'message': {'content': [
            {'toolUse': {'name': 'speak', 'input': {'messages': []}}},
        ]}}})
