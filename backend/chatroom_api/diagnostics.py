"""Inference failures are diagnostics, not participant conversation content."""
import re


def is_inference_diagnostic(event: dict) -> bool:
    return event.get('type') in {'system', 'error'} and (
        event.get('subtype') == 'inference_error'
        or str(event.get('content', '')).startswith('Chatroom server error:')
    )


def public_diagnostics(events: list[dict], enabled: bool) -> dict:
    if not enabled:
        return {}
    diagnostics = []
    for event in events:
        if not is_inference_diagnostic(event):
            continue
        code = event.get('error_code') or str(event.get('content', '')).removeprefix('Chatroom server error:').strip()
        # Preview is a presentation flag, not an auth boundary. Never return
        # exceptions, request payloads or credentials through this channel.
        if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', code):
            code = 'inference_error'
        diagnostics.append({
            'event_id': event.get('event_id') or event.get('event_key'),
            'timestamp': event.get('timestamp', 0),
            'code': code,
            'ai_participant_id': event.get('ai_participant_id'),
            'ai_name': event.get('ai_name') or 'AI',
        })
    return {'diagnostics': diagnostics}
