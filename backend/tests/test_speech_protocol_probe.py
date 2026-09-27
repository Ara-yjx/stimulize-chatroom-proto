"""Offline contract tests for the opt-in diagnostic probe, not live model tests."""
import pytest
from scripts.probe_speech_protocols import parse_json_text
from scripts.stress_speech_protocols import request_for


def response(text):
    return {'output': {'message': {'content': [{'text': text}]}}}


@pytest.mark.parametrize('text,expected,fenced', [
    ('{"messages":["Hello"]}', ['Hello'], False),
    ('{"messages":[]}', [], False),
    ('```json\n{"messages":["Hello"]}\n```', ['Hello'], True),
    ('```\n{"messages":[]}\n```', [], True),
])
def test_probe_accepts_only_explicit_json_messages(text, expected, fenced):
    assert parse_json_text(response(text)) == (expected, fenced)


@pytest.mark.parametrize('text', [
    'Hello', 'speak(messages: ["Hello"])', '', '{}', '{"messages":null}',
    '{"messages":"[\\"Hello\\"]"}', '{"messages":[42]}',
    '{"messages":[""]}', '{"messages":["unfinished',
    'Here is the JSON: {"messages":[]}', '{"messages":[],"extra":true}',
    '```python\n{"messages":[]}\n```',
    '```json\n{"messages":[]}\n```\nExtra prose',
])
def test_probe_does_not_turn_invalid_output_into_silence(text):
    with pytest.raises(ValueError):
        parse_json_text(response(text))


@pytest.mark.parametrize('protocol', ['json', 'auto'])
@pytest.mark.parametrize('scenario', ['speech', 'silence', 'forced'])
def test_stress_request_protocol_and_budget(protocol, scenario):
    request = request_for('test-model', protocol, scenario, 4)
    assert request['inferenceConfig']['maxTokens'] == 2048
    if protocol == 'json':
        assert 'toolConfig' not in request
        assert 'Return exactly one JSON' in request['system'][0]['text']
    else:
        assert request['toolConfig']['toolChoice'] == {'auto': {}}
    assert '<history>' in request['messages'][0]['content'][0]['text']
