"""Opt-in, small paid Bedrock probes. No database writes or runtime changes."""
import argparse
import json
from pathlib import Path
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from chatroom_api.prompts.speech_scaffold import SPEAK_TOOL_CONFIG, parse_speak_tool_call


def parse_json_text(response):
    blocks = response.get('output', {}).get('message', {}).get('content', [])
    text = ''.join(block['text'] for block in blocks if 'text' in block).strip()
    fenced = text.startswith('```')
    if fenced:
        lines = text.splitlines()
        if lines[0] not in ('```', '```json') or lines[-1] != '```':
            raise ValueError('invalid_json_fence')
        text = '\n'.join(lines[1:-1]).strip()
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {'messages'}:
        raise ValueError('invalid_json_object')
    messages = data['messages']
    if not isinstance(messages, list) or not all(isinstance(m, str) and m.strip() for m in messages):
        raise ValueError('invalid_messages')
    return messages, fenced


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+', required=True)
    parser.add_argument('--region', default='us-east-2')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--protocol', choices=('all', 'json'), default='all')
    parser.add_argument('--repeat', type=int, choices=range(1, 6), default=1)
    parser.add_argument('--invoke', action='store_true', help='Required to incur inference charges')
    args = parser.parse_args()
    if not args.invoke:
        print(json.dumps({'models': args.models, 'max_calls': (2 if args.protocol == 'json' else 4) * len(args.models) * args.repeat,
                          'max_output_tokens_per_call': 512, 'region': args.region}))
        return
    args.report.parent.mkdir(parents=True, exist_ok=True)
    client = boto3.client('bedrock-runtime', region_name=args.region, config=Config(
        connect_timeout=5, read_timeout=35, retries={'total_max_attempts': 1}))
    records = []

    def probe(model, protocol, silent=False):
        instruction = ('Call the supplied speak tool. Never print a tool call or JSON as plain text.'
                       if protocol != 'json' else
                       'Return exactly one JSON object with the key "messages", an array of message strings. '
                       'Return {"messages":[]} to stay silent. Otherwise put only the actual chat messages '
                       'in the array. No Markdown fences, tool calls, explanations or other keys.')
        expected = [] if silent else ['Hello, Alice!']
        user = ('Choose silence now. Return an empty messages array.' if silent else
                'Send exactly one message: Hello, Alice!')
        request = {'modelId': model, 'system': [{'text': instruction}],
                   'messages': [{'role': 'user', 'content': [{'text': user}]}],
                   'inferenceConfig': {'maxTokens': 512, 'temperature': 0.7}}
        if 'claude-opus-4-7' in model:
            request['inferenceConfig'].pop('temperature')
        if protocol != 'json':
            request['toolConfig'] = {**SPEAK_TOOL_CONFIG, 'toolChoice': (
                {'tool': {'name': 'speak'}} if protocol == 'tool' else {'auto': {}})}
        record = {'model': model, 'protocol': protocol, 'scenario': 'silence' if silent else 'speech'}
        started = time.monotonic()
        try:
            response = client.converse(**request)
            record.update(usage=response.get('usage'), stop_reason=response.get('stopReason'),
                          content=response.get('output', {}).get('message', {}).get('content', []),
                          request_id=response.get('ResponseMetadata', {}).get('RequestId'))
            try:
                if protocol == 'json':
                    messages, record['fenced'] = parse_json_text(response)
                else:
                    messages = parse_speak_tool_call(response)
                record['valid_schema'] = True
                record['matches_expected'] = messages == expected
            except (ValueError, TypeError, KeyError) as exc:
                record.update(valid_schema=False, parse_error=str(exc))
        except ClientError as exc:
            record['api_error'] = exc.response['Error']
        except Exception as exc:
            record['transport_error'] = type(exc).__name__
        record['seconds'] = round(time.monotonic()-started, 2)
        records.append(record)
        args.report.write_text(json.dumps(records, indent=2))
        args.report.chmod(0o600)
        print(json.dumps({k:v for k,v in record.items() if k not in ('content','request_id')}), flush=True)
        return record

    for model in args.models:
        for _ in range(args.repeat):
            if args.protocol == 'all':
                result = probe(model, 'tool')
                # Authorization or unknown-model failures cannot establish tool capability.
                error = result.get('api_error', {})
                if error and 'tool' not in error.get('Message', '').lower():
                    break
                if not result.get('valid_schema'):
                    probe(model, 'auto')
            probe(model, 'json')
            probe(model, 'json', silent=True)


if __name__ == '__main__':
    main()
