import importlib.util
import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError


def load_probe():
    path = Path(__file__).parents[1] / "scripts" / "probe_pdf_vision.py"
    spec = importlib.util.spec_from_file_location("probe_pdf_vision", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_does_not_call_aws(monkeypatch, tmp_path):
    probe = load_probe()
    pdf, output = tmp_path / "sample.pdf", tmp_path / "report.json"
    pdf.write_bytes(b"%PDF-synthetic")
    monkeypatch.setattr("sys.argv", ["probe", str(pdf), "--question", "What is visible?", "--output", str(output)])
    monkeypatch.setattr(probe.boto3, "client", lambda *a, **kw: pytest.fail("plan must not contact AWS"))
    probe.main()
    assert json.loads(output.read_text())["runs"] == []


@pytest.mark.parametrize("signature,kind", [
    (b"%PDF-synthetic", "document"),
    (b"\xff\xd8\xffsynthetic", "image"),
    (b"\x89PNG\r\n\x1a\nsynthetic", "image"),
])
def test_probe_uses_tool_and_cache_and_records_counting_failure(monkeypatch, tmp_path, signature, kind):
    probe = load_probe()
    pdf, output = tmp_path / "sample.pdf", tmp_path / "report.json"
    pdf.write_bytes(signature)
    requests = []

    class Client:
        def count_tokens(self, **kw):
            raise ClientError({"Error": {"Code": "ValidationException", "Message": "unsupported"}}, "CountTokens")

        def converse(self, **kw):
            requests.append(kw)
            return {
                "usage": {"inputTokens": 100, "cacheReadInputTokens": 1000, "outputTokens": 10},
                "stopReason": "tool_use",
                "output": {"message": {"content": [{"toolUse": {
                    "name": "speak", "input": {"messages": ["A visual detail"]},
                }}]}},
            }

    monkeypatch.setattr(probe.boto3, "client", lambda *a, **kw: Client())
    monkeypatch.setattr("sys.argv", ["probe", str(pdf), "--question", "What is visible?",
                                    "--output", str(output), "--invoke"])
    probe.main()
    assert len(requests) == 2
    blocks = requests[0]["messages"][0]["content"]
    document_index = next(i for i, b in enumerate(blocks) if kind in b)
    cache_index = next(i for i, b in enumerate(blocks) if "cachePoint" in b)
    assert document_index < cache_index
    if kind == "document":
        assert blocks[document_index]["document"]["citations"]["enabled"] is True
    else:
        assert "citations" not in blocks[document_index]["image"]
        assert blocks[document_index]["image"]["source"]["bytes"] == signature
    assert requests[0]["toolConfig"]["toolChoice"] == {"tool": {"name": "speak"}}
    report = json.loads(output.read_text())
    assert report["count_tokens_error"]["Code"] == "ValidationException"
    assert report["estimated_total_usd"] == pytest.approx(0.0015)
    assert report["runs"][0]["parsed_messages"] == ["A visual detail"]
    assert "%PDF-synthetic" not in output.read_text()
