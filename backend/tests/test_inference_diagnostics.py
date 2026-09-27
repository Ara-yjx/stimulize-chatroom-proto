import pytest
from chatroom_api import chat, mock_dynamo, tick_handler
from chatroom_api.diagnostics import public_diagnostics
from tests.test_chat import _setup_mocks, _seed_active_conversation, CLAIMS, CONVERSATION_ID


@pytest.mark.parametrize('debug', [None, '1'])
def test_poll_and_history_separate_diagnostics_without_losing_cursor(debug):
    _setup_mocks()
    _seed_active_conversation(events=[{
        'type': 'system', 'subtype': 'inference_error', 'timestamp': 1,
        'content': 'Chatroom server error: missing_tool_call', 'role': 'system',
        'ai_participant_id': 'a', 'ai_name': 'Alice',
    }])
    qp = {'debug': debug}
    for query in [chat.handle_chat_messages, chat.handle_chat_history]:
        status, page = query(qp, CLAIMS)
        assert status == 200
        assert page['events'] == []
        assert page.get('next_after') or page.get('latest_cursor')
        if debug:
            assert page['diagnostics'][0]['code'] == 'missing_tool_call'
            assert page['diagnostics'][0]['ai_name'] == 'Alice'
        else:
            assert 'diagnostics' not in page


def test_diagnostic_projection_never_returns_arbitrary_exception_content():
    event = {'type': 'system', 'subtype': 'inference_error',
             'content': 'secret prompt and stack', 'error_code': 'bad error with credentials'}
    assert public_diagnostics([event], True)['diagnostics'][0]['code'] == 'inference_error'
    assert 'secret' not in str(public_diagnostics([event], True))


def test_send_hides_legacy_errors_but_preserves_ordinary_system_notices(monkeypatch):
    _setup_mocks()
    _seed_active_conversation(events=[
        {'type': 'system', 'role': 'system', 'timestamp': 1,
         'content': 'Chatroom server error: ValidationException'},
        {'type': 'system', 'role': 'system', 'timestamp': 2,
         'content': 'Conversation started'},
    ])
    monkeypatch.setattr(chat, '_now_ms', lambda: 1000000)
    status, page = chat.handle_chat_send({'message': 'Hello'}, CLAIMS)
    assert status == 200
    assert [e['content'] for e in page['events']] == ['Conversation started', 'Hello']
    assert 'diagnostics' not in page


def test_diagnostic_does_not_enter_inference_context(monkeypatch):
    from tests.test_tick_handler_examples import _seed
    cid, start = _seed()
    mock_dynamo.append_events(cid, 'unused', [{
        'type': 'system', 'subtype': 'inference_error', 'timestamp': start + 1,
        'content': 'Chatroom server error: PRIVATE_DIAGNOSTIC', 'role': 'system',
    }])
    monkeypatch.setattr(tick_handler.time, 'time', lambda: start / 1000 + 30)
    calls = []
    def invoke(model, system, messages, **kwargs):
        calls.append(str(system) + str(messages))
        return {'messages': []}
    monkeypatch.setattr(tick_handler, 'invoke_speak_tool', invoke)
    tick_handler.handle_tick({'conversation_id': cid})
    assert calls and 'PRIVATE_DIAGNOSTIC' not in calls[0]
