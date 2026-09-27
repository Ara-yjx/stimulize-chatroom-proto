"""Explicit Converse speech capabilities and platform-only prompt rendering."""

import json
import re

JSON_MODEL_IDS = frozenset({
    'google.gemma-3-4b-it', 'google.gemma-3-12b-it', 'google.gemma-3-27b-it',
    'meta.llama3-1-8b-instruct-v1:0', 'meta.llama3-1-70b-instruct-v1:0',
    'deepseek.r1-v1:0',
})


def uses_json_speech(model_id: str) -> bool:
    prefix, separator, remainder = (model_id or '').strip().partition('.')
    normalized = remainder if separator and prefix in {'global', 'us', 'eu', 'apac', 'jp', 'au'} else (model_id or '').strip()
    return normalized in JSON_MODEL_IDS


def render_json_scaffold(scaffold: str) -> str:
    """Convert only code-owned scaffolds BEFORE researcher text is appended.

    Examples contain JSON arrays inside pseudo tool notation. Decode the arrays
    structurally so quotes, brackets inside messages and multiline examples are
    preserved. Never apply this conversion to a complete assembled prompt.
    """
    decoder = json.JSONDecoder()
    def example(match):
        start = match.end()
        messages, length = decoder.raw_decode(scaffold[start:])
        end = start + length
        if scaffold[end:end + 1] != ')' or not isinstance(messages, list):
            raise ValueError('Invalid platform speech example')
        return match.start(), end + 1, json.dumps({'messages': messages}, ensure_ascii=False)
    replacements = [example(match) for match in re.finditer(r'speak\(messages:\s*', scaffold)]
    for start, end, replacement in reversed(replacements):
        scaffold = scaffold[:start] + replacement + scaffold[end:]
    scaffold = scaffold.replace(
        'Always respond by calling the `speak` tool. If you have nothing to say, call it with an empty `messages` array. Never respond with plain text outside the tool call.',
        'Return only a JSON object with a messages array. If you have nothing to say, return {"messages": []}.',
    ).replace(
        'Always respond by calling the `speak` tool with one or more non-empty messages. Never stay silent and never respond with plain text outside the tool call.',
        'Return only a JSON object with one or more non-empty strings in its messages array. Never stay silent.',
    ).replace(
        '- always call the `speak` tool, with an empty `messages` array if you choose silence.',
        '- always return a JSON object, with an empty messages array if you choose silence.',
    ).replace(
        '(stay silent — call speak with empty messages array)', '{"messages": []}',
    ).replace('Use the speak tool to return', 'Return a JSON messages array containing')
    return scaffold + '\nOutput only {"messages": ["Your message"]} or {"messages": []}. Always use an array, even for one message. Finish the complete JSON object including its closing }. No extra fields, commentary or tool-call notation.\n'
