import hashlib
import io
import zipfile
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from chatroom_api.ai_batch import exporter, prompt_reference, store
from chatroom_api.prompts.speech_scaffold import get_scaffold_for_mode


def batch():
    return {
        "batch_job_id": "aib_reference", "owner_id": "9", "chatroom_id": "room",
        "created_at": "2026-09-12T00:00:00Z", "batch_count": 0, "status": "completed",
        "settings_snapshot": {
            "ai_count": 2, "human_count": 0, "model_id": "model", "temperature": 0.7,
            "topic_instruction": "Discuss campus life", "additional_prompt": "Be curious",
            "ai_personas": [{"internal_name": "planner", "persona": "Plan ahead", "temperature": 0}],
        },
    }


def test_reference_contains_full_scaffold_order_and_effective_settings():
    text = prompt_reference.render_prompt_reference(batch())
    assert get_scaffold_for_mode("ai_only", require_response=True).strip() in text
    assert text.index("STEP 1") < text.index("STEP 2") < text.index("CACHE BOUNDARY") < text.index("STEP 3")
    for expected in ("Plan ahead", "Be curious", "Discuss campus life", "Effective temperature: 0.0",
                     "Effective model: model", "Maximum turns: 100", "not each inference request"):
        assert expected in text
    assert len(prompt_reference.source_hash()) == 64


def test_assistant_multiai_includes_both_scaffold_variants():
    row = batch()
    row["settings_snapshot"].update(ai_count=3, mimic_human=False, ai_personas=[])
    text = prompt_reference.render_prompt_reference(row)
    for required in (True, False):
        assert get_scaffold_for_mode("ai_only", mimic_human=False, require_response=required).strip() in text


def test_storage_is_create_only_and_retry_keeps_first_content(monkeypatch):
    s3, table = Mock(), Mock()
    monkeypatch.setattr(store, "_get_s3", lambda: s3)
    monkeypatch.setattr(store, "_get_batch_table", lambda: table)
    store.save_prompt_reference(batch(), "lease", "original")
    assert s3.put_object.call_args.kwargs["IfNoneMatch"] == "*"
    assert table.update_item.call_args.kwargs["ExpressionAttributeValues"][":hash"] == hashlib.sha256(b"original").hexdigest()
    s3.put_object.side_effect = ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
    s3.get_object.return_value = {"Body": io.BytesIO(b"original")}
    store.save_prompt_reference(batch(), "new-lease", "changed template")
    assert table.update_item.call_args.kwargs["ExpressionAttributeValues"][":hash"] == hashlib.sha256(b"original").hexdigest()


def test_archive_reuses_exact_saved_bytes_and_detects_corruption(monkeypatch, tmp_path):
    row = batch()
    original = prompt_reference.render_prompt_reference(row).encode()
    row.update(prompt_reference_key="prompt-references/ref", prompt_reference_sha256=hashlib.sha256(original).hexdigest())
    s3 = Mock()
    s3.get_object.side_effect = lambda **kw: {"Body": io.BytesIO(original)}
    monkeypatch.setattr(store, "_get_s3", lambda: s3)
    monkeypatch.setattr(prompt_reference, "render_prompt_reference", lambda *_: pytest.fail("must not regenerate"))
    for _ in range(2):
        archive_bytes, _ = exporter.build_export_archive(row)
        path = tmp_path / "export.zip"
        path.write_bytes(archive_bytes)
        with zipfile.ZipFile(path) as archive:
            assert archive.read("info/prompt.txt") == original
            assert "info/prompt.json" not in archive.namelist()
    s3.get_object.side_effect = lambda **kw: {"Body": io.BytesIO(b"corrupted")}
    with pytest.raises(ValueError, match="integrity"):
        exporter.build_export_archive(row)


def test_legacy_notice_and_storage_failure(monkeypatch):
    assert b"unavailable" in store.read_prompt_reference(batch())
    s3 = Mock()
    s3.put_object.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
    monkeypatch.setattr(store, "_get_s3", lambda: s3)
    with pytest.raises(ClientError):
        store.save_prompt_reference(batch(), "lease", "text")
