import pytest

from chatroom_api.tick_handler import _build_tick_trigger_message, _room_silence_ms, _requires_idle_follow_up


def render(events, **kwargs):
    return _build_tick_trigger_message(
        conv={'events': events}, candidate_id='ai1', now_ms=100_000, **kwargs,
    )['content'][0]['text']


@pytest.mark.parametrize('identity', ['ai_participant_id', 'session_id'])
def test_own_last_message_and_elapsed_time(identity):
    text = render([{'type': 'message', 'role': 'ai', identity: 'ai1',
                    'timestamp': 93_000}])
    assert 'yours, sent 7 seconds ago' in text
    assert 'No other participant' in text
    assert 'not a new participant message' in text
    assert 'Do not repeat an answer' in text
    assert 'If you choose silence' in text


def test_ignore_system_and_future_messages():
    text = render([
        {'type': 'message', 'role': 'ai', 'ai_participant_id': 'ai1', 'timestamp': 93_000},
        {'type': 'system', 'timestamp': 95_000},
        {'type': 'message', 'role': 'human', 'session_id': 'h1', 'timestamp': 101_000},
    ])
    assert 'yours, sent 7 seconds ago' in text


@pytest.mark.parametrize('role,identity', [('human', 'session_id'), ('ai', 'ai_participant_id')])
def test_other_participant_not_misreported_as_self(role, identity):
    text = render([{'type': 'message', 'role': role, identity: 'other', 'timestamp': 98_000}])
    assert 'another participant, sent 2 seconds ago' in text
    assert 'No other participant' not in text


def test_empty_history():
    assert 'no visible chat messages yet' in render([])


def test_long_silence_does_not_force_waiting():
    text = render([{'type': 'message', 'role': 'ai', 'ai_participant_id': 'ai1', 'timestamp': 30_000}])
    assert 'sent 70 seconds ago' in text
    assert 'follow-up instructions' in text
    assert 'stay silent now' not in text


@pytest.mark.parametrize('flag', ['require_response', 'idle_follow_up'])
def test_required_response_policy_preserved(flag):
    assert 'at least one non-empty message' in render([], **{flag: True})


def test_json_trigger_keeps_provider_protocol():
    text = render([], model_id='google.gemma-3-4b-it')
    assert 'Return a JSON object' in text
    assert '{"messages": []}' in text
    assert '`speak`' not in text


@pytest.mark.parametrize('elapsed,forced', [(29999, False), (30000, True), (67000, True)])
def test_nudge_boundary(elapsed, forced):
    text = render([], room_silence_ms=elapsed)
    assert ('Break the silence now' in text) is forced
    assert ('at least one non-empty message' in text) is forced


def test_room_clock_ignores_system_and_future_events_and_resets_on_any_message():
    conv = {'started_at': '1970-01-01T00:00:10+00:00', 'events': [
        {'type': 'message', 'role': 'ai', 'timestamp': 70_000},
        {'type': 'system', 'timestamp': 99_000},
        {'type': 'message', 'timestamp': 110_000},
    ]}
    assert _room_silence_ms(conv, 100_000) == 30_000
    conv['events'].append({'type': 'message', 'role': 'human', 'timestamp': 95_000})
    assert _room_silence_ms(conv, 100_000) == 5_000


def test_empty_and_resumed_room_clock():
    conv = {'started_at': '1970-01-01T00:00:10+00:00', 'events': []}
    assert _room_silence_ms(conv, 40_000) == 30_000
    conv.update(resumable=True, active_episode_started_at='1970-01-01T00:01:30+00:00')
    conv['events'] = [{'type': 'message', 'timestamp': 50_000}]
    assert _room_silence_ms(conv, 100_000) == 10_000


def test_single_assistant_and_ai_only_excluded_from_room_nudge():
    conv = {'started_at': '1970-01-01T00:00:10+00:00', 'chatroom_setting': {
        'human_count': 1, 'ai_count': 1, 'mimic_human': False,
    }}
    assert _room_silence_ms(conv, 100_000) is None
    conv['chatroom_setting']['ai_count'] = 2
    assert _room_silence_ms(conv, 100_000) == 90_000
    conv['chatroom_setting']['human_count'] = 0
    assert _room_silence_ms(conv, 100_000) is None


@pytest.mark.parametrize('elapsed,expected', [(30_000, False), (59_999, False), (60_000, True)])
def test_assistant_retains_sixty_second_boundary(elapsed, expected):
    setting = {'human_count': 1, 'ai_count': 1, 'mimic_human': False}
    event = {'type': 'message', 'role': 'ai', 'timestamp': 10_000}
    conv = {'chatroom_setting': setting, 'events': [event]}
    assert _room_silence_ms(conv, 10_000 + elapsed) is None
    assert _requires_idle_follow_up(conv, setting, 10_000 + elapsed) is expected
    event['message_kind'] = 'idle_follow_up'
    assert not _requires_idle_follow_up(conv, setting, 10_000 + elapsed)
