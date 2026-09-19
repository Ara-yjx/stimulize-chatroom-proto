"""Readable initialization-time reference, not an executable prompt snapshot."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from chatroom_api.prompts.construction import build_semi_static_setup_blocks
from chatroom_api.prompts.speech_scaffold import get_scaffold_for_mode
from chatroom_api.settings import normalize_persona_entries


def code_block(text: str) -> str:
    # User prompts and TXT files can contain Markdown fences themselves.
    longest = max((len(match) for match in re.findall(r'`+', text)), default=0)
    fence = '`' * max(3, longest + 1)
    return f'{fence}text\n{text}\n{fence}'


def source_hash() -> str:
    """Identify the deployed prompt-related source, including uncommitted builds."""
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for name in (
        "prompts/speech_scaffold.py", "prompts/ai_only.py", "prompts/construction.py",
        "ai_batch/prompt_reference.py", "ai_batch/worker.py",
        "conversation.py", "settings.py", "bedrock_client.py",
        "ai_participants.py", "prompt_attachments.py",
    ):
        digest.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    return digest.hexdigest()


def render_prompt_reference(batch: dict) -> str:
    setting = batch["settings_snapshot"]
    model = setting.get("model_id", "")
    temperature = setting.get("temperature")
    if temperature is None:
        temperature = 0.7
    count = int(setting["ai_count"])
    required = count == 2
    lines = [
        "# AI Conversation Prompt Reference", "", "## How to Read This File", "",
        "Each turn sends shared instructions, the selected AI's setup, and history.",
        "The API receives system/messages/tools, not one concatenated string.",
        "This file follows that order; dynamic history and names are placeholders.",
        "It records templates at batch initialization, not each inference request.",
        "A deployment during a batch may change later worker instructions.",
        "It does not contain hidden reasoning or guarantee reproducible outputs.",
        "",
        f"- Batch: `{batch['batch_job_id']}`",
        f"- Batch created at: {batch.get('created_at', 'unknown')}",
        "- Reference format version: 2 (Markdown)",
        f"- Prompt-related source SHA-256: `{source_hash()}`",
        "", "## Request Settings (not literal prompt text)", "",
        f"- Default model: {model}", f"- Default temperature: {temperature}",
        f"- AI count: {count}", "- Style: semi-formal peer discussion (mimic_human does not apply)",
        f"- Message length guidance (optional): {str(setting['max_message_chars']) + ' characters' if setting.get('max_message_chars') is not None else 'not set'}",
        f"- Max messages: {setting.get('max_turns', 100)}",
        f"- Max characters: {setting.get('max_total_chars', 20000)} (last message may exceed this)", "",
        "Each call includes current accepted-message/character progress after the cache checkpoint.",
        "The target length and turn ceiling stop generation; they are not a request to fill that length.",
        "", "## STEP 1 - Shared System Instructions and Examples", "",
        code_block(get_scaffold_for_mode("ai_only", ai_count=count, require_response=required).strip()),
    ]
    if count > 2:
        lines.extend([
            "", "### Forced-response variant (if all candidates stay silent)", "",
            code_block(get_scaffold_for_mode("ai_only", ai_count=count, require_response=True).strip()),
        ])
    lines.extend([
        "", "## STEP 2 - Setup for the Selected AI", "",
        "Only one variant is inserted per call; persona assignment and names vary by conversation.",
        "See each conversation's participants in its JSON history for actual assignments and names.",
    ])
    personas = normalize_persona_entries(
        setting.get("ai_personas") or [], default_model_id=model,
        default_temperature=temperature,
    ) or [{"persona": "", "model_id": model, "temperature": temperature}]
    for index, persona in enumerate(personas, 1):
        lines.extend([
            "", f"### Persona {index}", "",
            f"- Internal name: {persona.get('internal_name') or '(unconfigured)'}",
            f"- Configured display name: {persona.get('nickname') or '(Participant_001, Participant_002, ... by assigned AI slot)'}",
            f"- Effective model: {persona.get('model_id') or model}",
            f"- Effective temperature: {persona.get('temperature') if persona.get('temperature') is not None else temperature}", "",
            "Setup text:", "",
        ])
        setup = build_semi_static_setup_blocks(
                setting, persona.get("persona") or "", "[selected AI name]",
                ["[selected AI name]", "[other participant names]"],
            )
        additional = (setting.get("additional_prompt") or "").strip()
        if additional:
            setup.append(additional)
        lines.append(code_block('\n\n'.join(setup)))
        from chatroom_api.prompt_attachments import selected_assets, read_asset_bytes
        assets = selected_assets(setting, persona)
        if assets:
            lines.extend(['', '#### Attachment Inputs (in request order)', '',
                'Shared files then persona files; a reused library ID appears once.',
                'Native user-message blocks precede the cache checkpoint; binary content is not reproduced here.'])
        for index, asset in enumerate(assets, 1):
            scope = 'shared' if asset['id'] in setting.get('prompt_attachment_ids', []) else 'persona'
            lines.append(f"Reference {index}: {asset.get('original_name', asset['id'])} [{scope}; {asset['format']}; {asset['byte_size']} bytes; SHA-256 {asset['sha256']}]")
            if asset['format'] == 'txt':
                import boto3
                from botocore.config import Config
                from chatroom_api import config
                raw = read_asset_bytes(asset, s3=boto3.client('s3', config=Config(connect_timeout=5, read_timeout=10)),
                                       bucket=config.PROMPT_ATTACHMENT_BUCKET)
                lines.extend(['', code_block(raw.decode('utf-8-sig')), ''])
    lines.extend([
        "", "## CACHE BOUNDARY (for supported models)", "",
        "A checkpoint follows the setup. Matching prefix computation may be reused, not previous answers.",
        "For models without our cache support, setup and formatted history are in the system text,",
        "with Additional Prompt after that history instead of before it.",
        "", "## STEP 3 - Conversation So Far (omitted from this reference)", "",
        code_block("<conversation-history>\n[Messages from this conversation so far]\n</conversation-history>"), "",
        "The same history is currently also sent as role-based messages:",
        "assistant = this AI's own messages; user = other participants' messages.",
        "", "## STEP 4 - Current Progress and Turn Instruction", "",
        "Dynamic progress is included on every call, after the cache boundary, for example:",
        code_block("Conversation progress: 12/100 messages; 3000/10000 characters used."), "",
        "Only accepted AI messages count; silence, failed calls, and system events do not.",
        "When message-length guidance is set, this also says:", "",
        code_block("Aim for at most N characters in your message."), "",
        "Two AIs alternate and must each produce one message." if required else
        "Other candidates are tried in random order and may stay silent; if all do, one is required to speak.",
        "A continuation instruction may be appended, for example:",
        code_block("Continue the conversation now with exactly one non-empty message."), "",
        "Long messages are not truncated or retried solely because of this guidance.",
        "", "## STEP 5 - Response Contract", "",
        "A separate speak tool definition requires a messages array containing the response.",
        "At most one message is accepted per call; mandatory turns cannot return an empty array.",
        "The application extracts the message text for the conversation history.",
    ])
    return "\n".join(lines) + "\n"
