#!/usr/bin/env python3
"""Opt-in real Bedrock conversation, with only local state and request traces.

Run from backend with PYTHONPATH=.: python scripts/early_completion_conversation.py
--state ../.local/example.json run --invoke. Other commands: step, show, append.
Initial settings come from --settings JSON, or the bounded demo defaults below.
No management API, DDB, RDS, or credits API is called. Each conversation has a
55-second deadline and every paid call is preserved locally, even late/errors.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import boto3
from botocore.config import Config

from chatroom_api import bedrock_client
from chatroom_api.ai_batch.completion import current_ticks, early_completion_enabled, limit_reason
from chatroom_api.ai_batch.scheduler import next_decision
from chatroom_api.ai_batch.worker import _build_request
from chatroom_api.ai_participants import build_ai_participants
from chatroom_api.participants import participant_id


def save(path: Path, state: dict) -> None:
    """Preserve traces without publishing them; replace the local checkpoint."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding='utf-8')
    temp.chmod(0o600)
    temp.replace(path)


def new_state(setting: dict) -> dict:
    """Use real participant/prompt construction, but no remote persistence."""
    early_completion_enabled(setting)
    if setting.get('human_count') != 0 or not 2 <= int(setting['ai_count']) <= 7:
        raise ValueError('Local runner supports 2-7 AI-only participants')
    if setting.get('prompt_attachment_ids') or any(p.get('prompt_attachment_ids') for p in setting.get('ai_personas', [])):
        raise ValueError('Use isolated cloud integration for attachments; this runner has no S3 reads')
    participants = build_ai_participants(setting, int(setting['ai_count']),
        ai_id_factory=lambda i: f'ai-{i}', sequential_nicknames=True, include_avatars=False)
    return {'setting': setting, 'events': [], 'calls': [], 'conversation': {
        'conversation_id': 'local-only', 'participants': participants, 'next_turn': 0,
        'message_count': 0, 'total_chars': 0, 'state_version': 0, 'status': 'running',
        'ai_tick_history': {'message_revision': 0, 'ticks': []},
    }}


def append_message(state: dict, ai_id: str, text: str) -> None:
    """Manual/new speech starts a new revision, invalidating all old consent."""
    conv = state['conversation']
    participant = next(p for p in conv['participants'] if participant_id(p) == ai_id)
    if not text.strip():
        raise ValueError('Message must not be blank')
    state['events'].append({'type': 'message', 'role': 'ai', 'ai_participant_id': ai_id,
        'sender': participant['nickname'], 'content': text, 'timestamp': int(time.time() * 1000)})
    conv.update(next_turn=conv['next_turn'] + 1, message_count=conv['message_count'] + 1,
                total_chars=conv['total_chars'] + len(text), last_speaker_id=ai_id,
                state_version=conv['state_version'] + 1)
    conv['ai_tick_history'] = {'message_revision': conv['next_turn'], 'ticks': []}


def run_tick(state: dict) -> None:
    """One real request using production builders/adapters, with local commits."""
    conv, setting = state['conversation'], state['setting']
    reason = limit_reason(conv['message_count'], conv['total_chars'], setting)
    if reason:
        conv.update(status='completed', completion_reason=reason)
        return
    deadline = state.setdefault('deadline_at', time.time() + 55)
    if time.time() >= deadline:
        conv['status'] = 'timed_out'
        return
    turn = next_decision(conv)
    if turn.participant is None:
        conv.update(status='completed', completion_reason='all_ai_agreed_to_end')
        return
    allow = early_completion_enabled(setting) and turn.allow_agreement
    model, temperature, system, messages = _build_request(conv, setting, turn.participant,
        state['events'], require_message=turn.require_message, allow_agreement=allow)
    trace = {'participant_id': participant_id(turn.participant), 'model_id': model,
             'require_message': turn.require_message, 'allow_agreement': allow,
             'system': system, 'messages': messages, 'temperature': temperature}
    state['calls'].append(trace)
    # Keep single calls inside the local budget; SDK retries are disabled.
    remaining = max(1, deadline - time.time())
    bedrock_client._deadline_client = boto3.client('bedrock-runtime', region_name='us-east-2',
        config=Config(connect_timeout=min(3, remaining), read_timeout=remaining, retries={'total_max_attempts': 1}))
    def before_attempt():
        if time.time() >= deadline:
            raise TimeoutError('Local conversation deadline reached')
    started = time.monotonic()
    send = bedrock_client._bounded_converse
    def capture(client, **request):
        # Capture the actual native/auto/JSON transport, not a rebuilt example.
        trace['request'] = request
        return send(client, **request)
    bedrock_client._bounded_converse = capture
    try:
        result = bedrock_client.invoke_speak_tool(model, system, messages, temperature=temperature,
            require_message=turn.require_message, max_messages=1, allow_agreement=allow, before_attempt=before_attempt)
        trace['result'] = result
        trace['elapsed_seconds'] = round(time.monotonic() - started, 3)
    except Exception as exc:
        trace['error'] = str(exc)
        conv['status'] = 'timed_out' if time.time() >= deadline else 'failed'
        return
    finally:
        bedrock_client._bounded_converse = send
    if time.time() >= deadline:
        trace['discarded'] = 'deadline'
        conv['status'] = 'timed_out'
        return
    if result.get('output_error'):
        conv.update(status='failed', error=result['output_error'])
        return
    action = result['outcome']
    if action == 'speech':
        append_message(state, participant_id(turn.participant), result['messages'][0].strip())
        reason = limit_reason(conv['message_count'], conv['total_chars'], setting)
        if reason:
            conv.update(status='completed', completion_reason=reason)
    else:
        ticks = current_ticks(conv) + [{'ai_participant_id': participant_id(turn.participant), 'action': action}]
        conv.update(ai_tick_history={'message_revision': conv['next_turn'], 'ticks': ticks}, state_version=conv['state_version'] + 1)
        if allow and next_decision(conv).participant is None:
            conv.update(status='completed', completion_reason='all_ai_agreed_to_end')


def main() -> None:
    """Bounded commands support step-by-step inspection and free-running tests."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--settings', type=Path)
    parser.add_argument('--model', default='global.anthropic.claude-sonnet-4-6')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('run', 'step'):
        command = sub.add_parser(name)
        command.add_argument('--invoke', action='store_true', help='Required: incurs Bedrock charges')
    sub.add_parser('show')
    append = sub.add_parser('append')
    append.add_argument('--ai-id', required=True)
    append.add_argument('--text', required=True)
    args = parser.parse_args()
    setting = json.loads(args.settings.read_text()) if args.settings else {
        'human_count': 0, 'ai_count': 2, 'model_id': args.model, 'temperature': 0.7,
        'max_turns': 8, 'max_total_chars': 2000, 'max_message_chars': 180,
        'topic_instruction': 'Choose one practical way a library could reduce electricity use, explaining its main trade-off.',
    }
    state = json.loads(args.state.read_text()) if args.state.exists() else new_state(setting)
    if args.command == 'append':
        if state['conversation']['status'] != 'running':
            parser.error('Use a new local file for a new conversation; terminal state is immutable')
        append_message(state, args.ai_id, args.text)
    elif args.command in {'step', 'run'}:
        if not args.invoke:
            parser.error('--invoke is required for real Bedrock calls')
        for _ in range(1 if args.command == 'step' else 20):
            if state['conversation']['status'] != 'running':
                break
            run_tick(state)
            save(args.state, state)
    save(args.state, state)
    conv = state['conversation']
    print(json.dumps({'status': conv['status'], 'completion_reason': conv.get('completion_reason'),
        'messages': conv['message_count'], 'inferences': len(state['calls']), 'trace': str(args.state)}, indent=2))


if __name__ == '__main__':
    main()
