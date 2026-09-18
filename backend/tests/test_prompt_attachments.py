from copy import deepcopy
from decimal import Decimal
from io import BytesIO
from hashlib import sha256
from unittest.mock import Mock

import boto3
import pytest

from chatroom_api.prompt_attachments import (
    AttachmentError, MANIFEST_FIELD, build_attachment_messages,
    check_serialized_request, public_setting, validate_room_snapshot, selected_assets,
)

MODEL = "verified-model"
SHARED = "paid_11111111-1111-4111-8111-111111111111"
PERSONA = "paid_22222222-2222-4222-8222-222222222222"
OTHER = "paid_33333333-3333-4333-8333-333333333333"


def fixture():
    blobs = {SHARED: b"shared", PERSONA: b"persona", OTHER: b"other private"}
    manifest = {key: {"id": key, "format": "txt", "byte_size": len(raw),
                      "s3_key": f"assets/{key}", "sha256": sha256(raw).hexdigest()}
                for key, raw in blobs.items()}
    setting = {"model_id": MODEL, "human_count": 1, "prompt_attachment_ids": [SHARED],
               "ai_personas": [{"prompt_attachment_ids": [PERSONA]},
                               {"prompt_attachment_ids": [OTHER]}], MANIFEST_FIELD: manifest}
    s3 = Mock()
    s3.get_object.side_effect = lambda **kw: {"Body": BytesIO(blobs[kw["Key"].split("/")[1]])}
    return setting, s3


@pytest.mark.parametrize("human_count", [0, 1, 2])
@pytest.mark.parametrize("cached", [False, True])
def test_scoped_once_before_cache_without_mutation(human_count, cached):
    setting, s3 = fixture()
    setting["human_count"] = human_count
    content = [{"text": "setup"}]
    if cached:
        content.append({"cachePoint": {"type": "default"}})
    content.append({"text": "history"})
    messages = [{"role": "user", "content": content}]
    original = deepcopy(messages)
    result = build_attachment_messages(messages, setting, setting["ai_personas"][0],
                                       s3=s3, bucket="isolated", supported_models={MODEL})
    assert messages == original
    assert s3.get_object.call_count == 2
    text = " ".join(b.get("text", "") for b in result[0]["content"])
    assert "Reference 1:\nshared" in text
    assert "Reference 2:\npersona" in text
    assert "other private" not in text
    if cached:
        checkpoint = next(i for i, b in enumerate(result[0]["content"]) if "cachePoint" in b)
        assert "persona" in result[0]["content"][checkpoint - 1]["text"]


def test_no_attachments_is_exact_noop():
    s3 = Mock()
    messages = [{"role": "user", "content": [{"text": "hello"}]}]
    assert build_attachment_messages(messages, {}, {}, s3=s3, bucket="", supported_models=set()) is messages
    s3.get_object.assert_not_called()


def test_snapshot_resolution_is_trusted_and_immutable(monkeypatch):
    from chatroom_api import config
    from chatroom_api.prompt_attachments import prepare_setting
    setting, _ = fixture()
    trusted = deepcopy(setting[MANIFEST_FIELD])
    setting[MANIFEST_FIELD] = {'untrusted': 'ignored'}
    provider = Mock()
    provider.resolve_prompt_assets.return_value = trusted
    monkeypatch.setattr(config, 'PROMPT_ATTACHMENTS_ENABLED', True)
    monkeypatch.setattr(config, 'PROMPT_ATTACHMENT_MODELS', {MODEL})
    result = prepare_setting({'id': 'room', 'setting': setting}, provider)
    assert result[MANIFEST_FIELD] == trusted
    setting['prompt_attachment_ids'] = []
    assert result['prompt_attachment_ids'] == [SHARED]
    assert result[MANIFEST_FIELD][SHARED]['sha256'] == trusted[SHARED]['sha256']


def test_disabled_feature_leaves_legacy_inference_untouched(monkeypatch):
    from chatroom_api import config
    from chatroom_api.prompt_attachments import attach_for_inference, prepare_setting
    monkeypatch.setattr(config, 'PROMPT_ATTACHMENTS_ENABLED', False)
    provider = Mock()
    assert prepare_setting({'setting': {'model_id': 'legacy'}}, provider) == {'model_id': 'legacy'}
    provider.resolve_prompt_assets.assert_not_called()
    messages = [{'role': 'user', 'content': [{'text': 'hello'}]}]
    assert attach_for_inference(messages, {'model_id': 'legacy'}, {}) is messages


def test_opus47_omits_rejected_temperature(monkeypatch):
    from chatroom_api import config
    from chatroom_api.bedrock_client import _bounded_converse
    monkeypatch.setattr(config, 'PROMPT_ATTACHMENTS_ENABLED', False)
    client = Mock()
    _bounded_converse(client, modelId='global.anthropic.claude-opus-4-7',
                      inferenceConfig={'temperature': .7, 'maxTokens': 100})
    assert client.converse.call_args.kwargs['inferenceConfig'] == {'maxTokens': 100}


def test_same_library_asset_reused_across_scopes_once():
    setting, s3 = fixture()
    persona = setting["ai_personas"][0]
    persona["prompt_attachment_ids"] = [SHARED, PERSONA]
    result = build_attachment_messages([], setting, persona, s3=s3,
                                       bucket="isolated", supported_models={MODEL})
    assert s3.get_object.call_count == 2
    assert [a["id"] for a in selected_assets(setting, persona)] == [SHARED, PERSONA]
    assert sum("Reference 1:\nshared" == b.get("text") for b in result[0]["content"]) == 1


def test_all_models_checked_even_unselected_persona():
    setting, _ = fixture()
    setting["ai_personas"][1]["model_id"] = "unsupported"
    with pytest.raises(AttachmentError, match="All chatroom"):
        validate_room_snapshot(setting, {MODEL})


@pytest.mark.parametrize("mutation", ["missing", "oversize", "key", "duplicate", "hash"])
def test_manifest_rejection_before_fetch(mutation):
    setting, s3 = fixture()
    asset = setting[MANIFEST_FIELD][SHARED]
    if mutation == "missing":
        del setting[MANIFEST_FIELD][SHARED]
    elif mutation == "oversize":
        asset["byte_size"] = 100001
    elif mutation == "key":
        asset["s3_key"] = "somebody-elses-key"
    elif mutation == "hash":
        asset["sha256"] = "invalid"
    else:
        setting["prompt_attachment_ids"] *= 2
    with pytest.raises(AttachmentError):
        build_attachment_messages([], setting, {}, s3=s3, bucket="isolated", supported_models={MODEL})
    s3.get_object.assert_not_called()


def test_corrupt_bytes_fail_and_close_stream():
    setting, s3 = fixture()
    stream = BytesIO(b"wrong")
    s3.get_object.side_effect = None
    s3.get_object.return_value = {"Body": stream}
    with pytest.raises(AttachmentError, match="integrity"):
        build_attachment_messages([], setting, {}, s3=s3, bucket="isolated", supported_models={MODEL})
    assert stream.closed


def test_dynamodb_integral_decimal_snapshot():
    setting, s3 = fixture()
    for asset in setting[MANIFEST_FIELD].values():
        asset["byte_size"] = Decimal(asset["byte_size"])
    result = build_attachment_messages([], setting, {}, s3=s3,
                                       bucket="isolated", supported_models={MODEL})
    assert result[0]["role"] == "user"


@pytest.mark.parametrize("bad_size", [True, 5.0, Decimal("NaN"), Decimal("1.1")])
def test_invalid_size_types(bad_size):
    setting, _ = fixture()
    setting[MANIFEST_FIELD][SHARED]["byte_size"] = bad_size
    with pytest.raises(AttachmentError):
        validate_room_snapshot(setting, {MODEL})


def test_public_settings_do_not_leak_asset_references():
    setting, _ = fixture()
    output = public_setting(setting)
    assert MANIFEST_FIELD not in output and "prompt_attachment_ids" not in output
    assert all("prompt_attachment_ids" not in p for p in output["ai_personas"])
    assert MANIFEST_FIELD in setting


@pytest.mark.parametrize("fmt,raw", [("pdf", b"%PDF-1.7\nreference"),
                                    ("png", b"\x89PNG\r\n\x1a\nimage"),
                                    ("jpeg", b"\xff\xd8\xffimage")])
def test_native_blocks_with_neutral_names(fmt, raw):
    setting, s3 = fixture()
    asset = setting[MANIFEST_FIELD][SHARED]
    asset.update(format=fmt, byte_size=len(raw), sha256=sha256(raw).hexdigest(),
                 page_count=1, width=10, height=10, original_name="ignore-rules.pdf")
    s3.get_object.side_effect = lambda **_: {"Body": BytesIO(raw)}
    result = build_attachment_messages([], setting, {}, s3=s3,
                                       bucket="isolated", supported_models={MODEL})
    block = result[0]["content"][1]
    native = block["document" if fmt == "pdf" else "image"]
    assert native["source"]["bytes"] == raw
    if fmt == "pdf":
        assert native["name"] == "Reference 1"
        assert native["citations"]["enabled"] is True


@pytest.mark.parametrize("field,value", [("page_count", 11), ("byte_size", 4_500_001)])
def test_pdf_native_limits(field, value):
    setting, _ = fixture()
    setting[MANIFEST_FIELD][SHARED].update(format="pdf", byte_size=10, page_count=1)
    setting[MANIFEST_FIELD][SHARED][field] = value
    with pytest.raises(AttachmentError):
        selected_assets(setting, {})


def test_combined_page_limit_checks_persona_not_only_shared():
    setting, _ = fixture()
    for key in (SHARED, PERSONA):
        setting[MANIFEST_FIELD][key].update(format="pdf", byte_size=10, page_count=6)
    with pytest.raises(AttachmentError, match="Combined PDF"):
        validate_room_snapshot(setting, {MODEL})


def test_combined_raw_limit():
    setting, _ = fixture()
    for asset in setting[MANIFEST_FIELD].values():
        asset.update(format="pdf", byte_size=4_000_000, page_count=1)
    setting["prompt_attachment_ids"] = [SHARED, OTHER]
    with pytest.raises(AttachmentError, match="Combined attachments"):
        validate_room_snapshot(setting, {MODEL})


def test_real_serializer_guard_without_network(monkeypatch):
    client = boto3.client("bedrock-runtime", region_name="us-east-2",
                          aws_access_key_id="test", aws_secret_access_key="test")
    request = {"modelId": MODEL, "messages": [{"role": "user", "content": [
        {"image": {"format": "png", "source": {"bytes": b"x" * 300}}}]}]}
    check_serialized_request(request, client)
    monkeypatch.setattr("chatroom_api.prompt_attachments.MAX_REQUEST_BYTES", 350)
    with pytest.raises(AttachmentError, match="too large"):
        check_serialized_request(request, client)
