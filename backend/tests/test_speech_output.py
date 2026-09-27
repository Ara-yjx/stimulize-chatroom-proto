import pytest

from chatroom_api.bedrock_client import _speak_result, log_speak_result
from chatroom_api.gate import run_gate


def test_speech_request_uses_provider_budget_independent_of_character_guidance(monkeypatch):
    from chatroom_api import bedrock_client
    calls = []
    monkeypatch.setattr(bedrock_client, '_get_client', lambda: object())
    def converse(client, **request):
        calls.append(request)
        return response(['A useful response'])
    monkeypatch.setattr(bedrock_client, '_bounded_converse', converse)
    for guidance in [None, 1, 1000]:
        result = bedrock_client.invoke_speak_tool(
            'model', 'system', [], max_message_chars=guidance)
        assert result['outcome'] == 'speech'
    assert [r['inferenceConfig']['maxTokens'] for r in calls] == [2048, 2048, 2048]


def response(messages, stop='tool_use'):
    return {'stopReason': stop, 'output': {'message': {'content': [
        {'toolUse': {'name': 'speak', 'input': {'messages': messages}}}
    ]}}, 'usage': {'inputTokens': 10, 'outputTokens': 512},
        'ResponseMetadata': {'RequestId': 'request'}}


@pytest.mark.parametrize('messages,stop,required,outcome,error', [
    (['hello'], 'tool_use', False, 'speech', None),
    ([], 'tool_use', False, 'silence', None),
    ([], 'tool_use', True, 'error', 'required_speech_missing'),
    (['An unfinished sentence because'], 'max_tokens', True, 'speech', None),
    ([], 'max_tokens', False, 'error', 'truncated_without_message'),
    ([42], 'tool_use', False, 'error', 'invalid_messages'),
    ([''], 'tool_use', False, 'error', 'invalid_messages'),
    (['one', 'two'], 'tool_use', False, 'error', 'too_many_messages'),
])
def test_output_contract(messages, stop, required, outcome, error):
    result = _speak_result(response(messages, stop), require_message=required, max_messages=1)
    assert result['outcome'] == outcome
    assert result['output_error'] == error
    assert result['output_tokens'] == 512
    assert result['request_id'] == 'request'
    if outcome == 'speech':
        assert result['messages'] == messages


def test_plain_text_and_truncated_empty_tool_are_not_silence(caplog):
    raw = response([])
    raw['output']['message']['content'] = [{'text': 'private speak(messages: [hello])'}]
    result = _speak_result(raw, require_message=False, max_messages=5)
    assert result['output_error'] == 'missing_tool_call'
    log_speak_result(result, 'model', conversation_id='conversation', ai_participant_id='ai')
    assert 'missing_tool_call' in caplog.text
    assert 'request_id=request' in caplog.text
    assert 'private' not in caplog.text
    raw = response([], 'max_tokens')
    raw['output']['message']['content'][0]['toolUse']['input'] = {}
    assert _speak_result(raw, require_message=True, max_messages=1)['output_error'] == 'truncated_without_message'


@pytest.mark.parametrize('last_result', ['silent', 'error'])
def test_silence_and_errors_do_not_starve_other_candidates(last_result):
    conv = {'participants': [{'session_id': name, 'role': 'ai'} for name in ['a', 'b', 'c']],
            'ai_tick_state_by_participant_id': {}}
    chosen = []
    for now in range(1, 7):
        candidate = run_gate(conv, now).candidate_session_id
        chosen.append(candidate)
        conv['ai_tick_state_by_participant_id'][candidate] = {
            'last_evaluated_at': now, 'last_result': last_result}
    assert chosen == ['a', 'b', 'c', 'a', 'b', 'c']
