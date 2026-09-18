"""Opt-in six-call capability probe. No database, deployment or app data writes."""
import argparse
import json
from pathlib import Path
from io import BytesIO
import time
import boto3
from botocore.config import Config
from PIL import Image

MODELS = [
    'global.anthropic.claude-sonnet-4-6',
    'global.anthropic.claude-sonnet-4-5-20250929-v1:0',
    'global.anthropic.claude-sonnet-4-20250514-v1:0',
    'global.anthropic.claude-haiku-4-5-20251001-v1:0',
    'global.anthropic.claude-opus-4-7',
    'global.anthropic.claude-opus-4-6-v1',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', type=Path, required=True)
    parser.add_argument('--jpeg', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--model', choices=MODELS)
    args = parser.parse_args()
    if not args.execute:
        print('Plan: one four-format request per catalog Anthropic model, maxTokens=100, no retries')
        return
    pdf, jpeg = args.pdf.read_bytes(), args.jpeg.read_bytes()
    assert len(pdf) < 4500000 and len(jpeg) < 3750000
    png = BytesIO()
    Image.new('RGB', (64, 64), 'red').save(png, format='PNG')
    client = boto3.client('bedrock-runtime', region_name='us-east-2',
                          config=Config(connect_timeout=5, read_timeout=50, retries={'total_max_attempts': 1}))
    results = []
    for model in ([args.model] if args.model else MODELS):
        start = time.monotonic()
        try:
            response = client.converse(modelId=model,
                messages=[{'role': 'user', 'content': [
                    {'text': 'TXT reference: the keyword is ORCHARD. Briefly describe the PDF topic, JPEG objects, PNG color and keyword. Use speak.'},
                    {'document': {'format': 'pdf', 'name': 'Reference 1', 'source': {'bytes': pdf}, 'citations': {'enabled': True}}},
                    {'image': {'format': 'jpeg', 'source': {'bytes': jpeg}}},
                    {'image': {'format': 'png', 'source': {'bytes': png.getvalue()}}},
                ]}],
                toolConfig={'tools': [{'toolSpec': {'name': 'speak', 'description': 'Give your answer',
                    'inputSchema': {'json': {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer']}}}}],
                    'toolChoice': {'tool': {'name': 'speak'}}},
                inferenceConfig={'maxTokens': 100, **({} if 'claude-opus-4-7' in model else {'temperature': 0.7})})
            results.append({'model': model, 'accepted': True, 'usage': response['usage'],
                            'output': response['output'], 'seconds': round(time.monotonic()-start, 2)})
        except Exception as exc:
            results.append({'model': model, 'accepted': False, 'error': str(exc), 'seconds': round(time.monotonic()-start, 2)})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2))
        print(model, 'accepted' if results[-1]['accepted'] else results[-1]['error'], flush=True)


if __name__ == '__main__':
    main()
