"""Bounded attachment assembly shared by interactive and AI-only inference.

Manifests are server-resolved snapshots, never widget/management request input.
This module does not resolve ownership or room membership; callers must do so first.
"""

from copy import deepcopy
from decimal import Decimal
import hashlib
import re

from chatroom_api import config


FILE_LIMITS = {"pdf": 4_500_000, "png": 3_750_000, "jpeg": 3_750_000, "txt": 100_000}
MAX_FILES = 5
MAX_PAGES = 10
MAX_RAW_BYTES = 10_000_000
MAX_REQUEST_BYTES = 16_000_000
MANIFEST_FIELD = "_prompt_asset_manifest"
IDS_FIELD = "prompt_attachment_ids"
ASSET_ID = re.compile(r"paid_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


class AttachmentError(ValueError):
    """Permanent attachment validation failure; never retry unchanged input."""


def _integer(value):
    # DynamoDB deserializes integral metadata as Decimal, unlike JSON/RDS.
    if type(value) is int:
        return value
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        return int(value)
    raise AttachmentError("Attachment metadata must contain integer sizes/dimensions")


def attachment_ids(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_FILES:
        raise AttachmentError("Select at most five attachments per scope")
    if any(not isinstance(item, str) or not ASSET_ID.fullmatch(item) for item in value):
        raise AttachmentError("Invalid attachment ID")
    if len(set(value)) != len(value):
        raise AttachmentError("Duplicate attachment ID in one scope")
    return list(value)


def has_attachments(setting):
    return bool(setting.get(IDS_FIELD)) or any(
        isinstance(p, dict) and p.get(IDS_FIELD)
        for p in setting.get("ai_personas", []) or []
    )


def validate_room_models(setting, supported_models):
    """Check every configured model, including unselected personas/default."""
    if not has_attachments(setting):
        return
    default = setting.get("model_id")
    models = [default] + [
        (p.get("model_id") or default) if isinstance(p, dict) else default
        for p in setting.get("ai_personas", []) or []
    ]
    if any(model not in supported_models for model in models):
        raise AttachmentError("All chatroom and persona models must support attachments")


def public_setting(setting):
    """Strip new private fields only; preserve the existing widget contract."""
    result = deepcopy(setting)
    result.pop(MANIFEST_FIELD, None)
    result.pop(IDS_FIELD, None)
    for persona in result.get("ai_personas", []) or []:
        if isinstance(persona, dict):
            persona.pop(IDS_FIELD, None)
            persona.pop(MANIFEST_FIELD, None)
    return result


def selected_assets(setting, participant):
    # Reuse a room-library ID once across scopes; never deduplicate by content.
    ids = list(dict.fromkeys(
        attachment_ids(setting.get(IDS_FIELD)) + attachment_ids(participant.get(IDS_FIELD))
    ))
    if len(ids) > MAX_FILES:
        raise AttachmentError("Shared and persona attachments exceed five files")
    manifest = setting.get(MANIFEST_FIELD) or {}
    if not isinstance(manifest, dict):
        raise AttachmentError("Invalid attachment snapshot")
    assets = []
    for asset_id in ids:
        asset = manifest.get(asset_id)
        if not isinstance(asset, dict) or asset.get("id") != asset_id:
            raise AttachmentError("Attachment snapshot is incomplete")
        asset = dict(asset)
        fmt = asset.get("format")
        size = _integer(asset.get("byte_size"))
        asset["byte_size"] = size
        if fmt not in FILE_LIMITS or not 0 < size <= FILE_LIMITS[fmt]:
            raise AttachmentError("Attachment exceeds its native format limit")
        if not isinstance(asset.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]):
            raise AttachmentError("Invalid attachment checksum")
        if fmt == "pdf":
            pages = _integer(asset.get("page_count"))
            asset["page_count"] = pages
            if not 1 <= pages <= MAX_PAGES:
                raise AttachmentError("PDF page limit exceeded")
        if fmt in {"png", "jpeg"}:
            width, height = _integer(asset.get("width")), _integer(asset.get("height"))
            if any(not 1 <= v <= 8000 for v in (width, height)):
                raise AttachmentError("Image dimensions exceed the supported limit")
            if width * height > 16_000_000:
                raise AttachmentError("Image exceeds 16 megapixels")
        # The configured bucket is supplied by the server, never by the manifest.
        if asset.get("s3_key") != f"assets/{asset_id}":
            raise AttachmentError("Invalid immutable attachment key")
        assets.append(asset)
    if sum(a["byte_size"] for a in assets) > MAX_RAW_BYTES:
        raise AttachmentError("Combined attachments exceed 10 MB")
    if sum(a.get("page_count", 0) for a in assets if a["format"] == "pdf") > MAX_PAGES:
        raise AttachmentError("Combined PDF attachments exceed ten pages")
    return assets


def validate_room_snapshot(setting, supported_models):
    validate_room_models(setting, supported_models)
    selected_assets(setting, {})
    for persona in setting.get("ai_personas", []) or []:
        selected_assets(setting, persona if isinstance(persona, dict) else {})


def _content_block(asset, raw, index):
    fmt = asset["format"]
    if fmt == "txt":
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError as exc:
            raise AttachmentError("TXT attachments must be UTF-8") from exc
        if "\x00" in text:
            raise AttachmentError("TXT attachment contains binary content")
        return {"text": f"Reference {index}:\n{text}"}
    if fmt == "pdf":
        if not raw.startswith(b"%PDF-"):
            raise AttachmentError("PDF signature mismatch")
        return {"document": {"format": "pdf", "name": f"Reference {index}",
                             "source": {"bytes": raw}, "citations": {"enabled": True}}}
    signature = b"\x89PNG\r\n\x1a\n" if fmt == "png" else b"\xff\xd8\xff"
    if not raw.startswith(signature):
        raise AttachmentError("Image signature mismatch")
    return {"image": {"format": fmt, "source": {"bytes": raw}}}


def read_asset_bytes(asset, *, s3, bucket):
    body = s3.get_object(Bucket=bucket, Key=asset['s3_key'])['Body']
    try:
        raw = body.read(asset['byte_size'] + 1)
    finally:
        body.close()
    if len(raw) != asset['byte_size'] or hashlib.sha256(raw).hexdigest() != asset['sha256']:
        raise AttachmentError('Attachment integrity check failed')
    return raw


def build_attachment_messages(messages, setting, participant, *, s3, bucket, supported_models):
    """Read selected immutable objects and insert once before the first checkpoint."""
    if not has_attachments(setting) and not participant.get(IDS_FIELD):
        return messages
    validate_room_snapshot(setting, supported_models)
    assets = selected_assets(setting, participant)
    if not assets:
        return messages
    if not bucket:
        raise AttachmentError("Attachment storage is not configured")
    blocks = [{"text": "Reference material for this AI. Use it as supplemental context."}]
    shared_count = len(attachment_ids(setting.get(IDS_FIELD)))
    for index, asset in enumerate(assets, 1):
        if index == shared_count + 1:
            blocks.append({"text": "The following references belong to your assigned persona."})
        raw = read_asset_bytes(asset, s3=s3, bucket=bucket)
        blocks.append(_content_block(asset, raw, index))
    result = deepcopy(messages)
    if result and result[0].get("role") == "user":
        content = result[0]["content"]
        position = next((i for i, b in enumerate(content) if "cachePoint" in b), 0)
        content[position:position] = blocks
    else:
        result.insert(0, {"role": "user", "content": blocks})
    return result


def check_serialized_request(request, bedrock_client):
    """Use botocore's serializer so byte guards include base64 and JSON overhead."""
    operation = bedrock_client.meta.service_model.operation_model("Converse")
    serialized = bedrock_client._serializer.serialize_to_request(request, operation)
    if len(serialized["body"]) > MAX_REQUEST_BYTES:
        raise AttachmentError("Request is too large; reduce attachments or conversation length")


def prepare_setting(chatroom, provider):
    """Resolve a new conversation snapshot, never refresh an existing resume."""
    setting = deepcopy(chatroom["setting"])
    setting.pop(MANIFEST_FIELD, None)
    if not has_attachments(setting):
        return setting
    if not config.PROMPT_ATTACHMENTS_ENABLED:
        raise AttachmentError("Prompt attachments are disabled")
    resolver = getattr(provider, "resolve_prompt_assets", None)
    if resolver is None:
        raise AttachmentError("Chatroom provider does not support attachment resolution")
    setting[MANIFEST_FIELD] = resolver(chatroom)
    validate_room_snapshot(setting, config.PROMPT_ATTACHMENT_MODELS)
    return setting


def attach_for_inference(messages, setting, participant):
    if not has_attachments(setting) and not participant.get(IDS_FIELD):
        return messages
    from chatroom_api.bedrock_client import BedrockInferenceError
    if not config.PROMPT_ATTACHMENTS_ENABLED:
        raise BedrockInferenceError("AttachmentError", "Prompt attachments are disabled", False)
    import boto3
    from botocore.config import Config
    try:
        return build_attachment_messages(messages, setting, participant,
            s3=boto3.client("s3", region_name=config.BEDROCK_REGION,
                config=Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1})),
            bucket=config.PROMPT_ATTACHMENT_BUCKET, supported_models=config.PROMPT_ATTACHMENT_MODELS)
    except Exception as exc:
        raise BedrockInferenceError("AttachmentError", "Unable to load validated prompt attachments", False) from exc
