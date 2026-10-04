"""Protocol and scheduling invariants across histories, not helper snapshots."""

import json
import random

import pytest
from hypothesis import given, strategies as st

from chatroom_api.ai_batch.completion import early_completion_enabled
from chatroom_api.ai_batch.scheduler import next_decision
from chatroom_api.bedrock_client import _speak_result
from chatroom_api.prompts.construction import build_system_prompt


def conversation(count=3):
    return {'next_turn': 1, 'last_speaker_id': 'ai-0',
            'participants': [{'ai_participant_id': f'ai-{i}', 'role': 'ai'} for i in range(count)],
            'ai_tick_history': {'message_revision': 1, 'ticks': []}}


@given(count=st.integers(2, 7), seed=st.integers(0, 100000), first_silence=st.booleans())
def test_confirmation_round_is_bounded_and_last_speaker_really_is_last(count, seed, first_silence):
    conv, rng = conversation(count), random.Random(seed)
    called = []
    while True:
        turn = next_decision(conv, rng)
        if turn.participant is None:
            break
        ai_id = turn.participant['ai_participant_id']
        assert ai_id != 'ai-0' or len({t['ai_participant_id'] for t in conv['ai_tick_history']['ticks'] if t['action'] == 'agree_to_end'}) == count - 1
        action = 'silence' if first_silence and not turn.require_message else 'agree_to_end'
        conv['ai_tick_history']['ticks'].append({'ai_participant_id': ai_id, 'action': action})
        called.append(ai_id)
        assert len(called) <= 2 * count - 1
    assert called[-1] == 'ai-0'
    assert len(called) == (2 * count - 1 if first_silence and count > 2 else count)


@given(count=st.integers(2, 7), seed=st.integers(0, 100000))
def test_no_agreement_keeps_legacy_distribution_and_speech_resets_round(count, seed):
    conv, rng, tried = conversation(count), random.Random(seed), set()
    for _ in range(count - 1):
        turn = next_decision(conv, rng)
        ai_id = turn.participant['ai_participant_id']
        assert ai_id != 'ai-0' and ai_id not in tried
        assert turn.require_message == (count == 2)
        tried.add(ai_id)
        conv['ai_tick_history']['ticks'].append({'ai_participant_id': ai_id, 'action': 'silence'})
    forced = next_decision(conv, rng)
    assert forced.require_message and forced.participant['ai_participant_id'] in tried
    conv['ai_tick_history']['ticks'] = [{'ai_participant_id': p['ai_participant_id'], 'action': 'agree_to_end'} for p in conv['participants']]
    conv.update(next_turn=2, last_speaker_id=forced.participant['ai_participant_id'])
    # A stale round cannot complete a newer history, even before explicit cleanup.
    turn = next_decision(conv, rng)
    assert turn.participant is not None and turn.participant['ai_participant_id'] != conv['last_speaker_id']


def response(payload, json_protocol, stop='end_turn'):
    if json_protocol:
        block = {'text': json.dumps(payload)}
    else:
        name = 'agreeToEnd' if payload == {'action': 'agree_to_end'} else 'speak'
        block = {'toolUse': {'name': name, 'input': {} if name == 'agreeToEnd' else payload}}
    return {'output': {'message': {'content': [block]}}, 'stopReason': stop,
            'usage': {'inputTokens': 20, 'outputTokens': 10}}


@pytest.mark.parametrize('json_protocol', [False, True])
def test_consent_is_explicit_exclusive_and_never_inferred_from_truncation(json_protocol):
    def parse(payload, **kwargs):
        return _speak_result(response(payload, json_protocol, kwargs.pop('stop', 'end_turn')),
                             max_messages=1, json_protocol=json_protocol, **kwargs)
    accepted = parse({'action': 'agree_to_end'}, require_message=True, allow_agreement=True)
    assert accepted['outcome'] == 'agree_to_end' and accepted['messages'] == []
    for payload in ({'action': 'agree_to_end', 'messages': []}, {'messages': [], 'extra': 1}, {}, {'messages': 'hello'}):
        invalid = parse(payload, require_message=False, allow_agreement=True)
        assert invalid['output_error'] and invalid['input_tokens'] == 20
    assert parse({'messages': []}, require_message=False, allow_agreement=True)['outcome'] == 'silence'
    assert parse({'messages': []}, require_message=True, allow_agreement=True)['output_error']
    assert parse({'action': 'agree_to_end'}, require_message=True, allow_agreement=True, stop='max_tokens')['output_error'] == 'truncated_control_output'
    assert parse({'messages': ['I was about to']}, require_message=True, allow_agreement=True, stop='max_tokens')['outcome'] == 'speech'
    # Opening/disabled and human-AI calls never accept consent instead of speech.
    assert parse({'action': 'agree_to_end'}, require_message=True)['output_error']


def test_native_multiple_actions_and_json_duplicate_keys_fail_loudly():
    raw = response({'action': 'agree_to_end'}, False)
    raw['output']['message']['content'] += response({'messages': ['hello']}, False)['output']['message']['content']
    assert _speak_result(raw, require_message=False, max_messages=1, allow_agreement=True)['output_error'] == 'multiple_tool_calls'
    raw = response({}, True)
    raw['output']['message']['content'] = [{'text': '{"action":"agree_to_end","action":"agree_to_end"}'}]
    assert _speak_result(raw, require_message=False, max_messages=1, allow_agreement=True, json_protocol=True)['output_error'] == 'duplicate_json_key'


def test_defaults_are_strict_and_rules_are_ai_only_without_rewriting_researcher_prompts():
    assert early_completion_enabled({}) is True
    for invalid in (None, 0, 1, 'true', 'false'):
        with pytest.raises(ValueError, match='allow_early_completion'):
            early_completion_enabled({'allow_early_completion': invalid})
    setting = {'ai_count': 2, 'topic_instruction': 'Researcher says agreeToEnd() verbatim', 'model_id': 'google.gemma-3-27b-it'}
    prompt = build_system_prompt('ai_only', setting, '', 'A', '')
    assert '{"action":"agree_to_end"}' in prompt
    assert 'Researcher says agreeToEnd() verbatim' in prompt
    assert 'exactly one non-empty message on each call' not in prompt
    setting['topic_instruction'] = ''
    for mode, configured in [('ai_only', {'allow_early_completion': False}), ('group', {}), ('one_on_one', {})]:
        prompt = build_system_prompt(mode, {**setting, **configured}, '', 'A', '')
        assert 'agree_to_end' not in prompt and 'agreeToEnd' not in prompt
