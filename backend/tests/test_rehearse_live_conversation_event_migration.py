import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "scripts"
    / "rehearse_live_conversation_event_migration.py"
)
SPEC = importlib.util.spec_from_file_location("live_rehearsal", SCRIPT)
rehearsal = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(rehearsal)


def _args(stage="prepare"):
    return [
        "--stage", stage,
        "--source-table", "chatroom-conversations",
        "--region", "us-east-2",
        "--run-id", "20260815-test",
        "--cutover-at-ms", "1786752000000",
        "--work-dir", "/tmp/rehearsal-test",
    ]


def test_manifest_is_deterministic_and_names_are_isolated():
    manifest = rehearsal.build_manifest(
        source_table="chatroom-conversations",
        region="us-east-2",
        run_id="20260815-test",
        cutover_at_ms=1786752000000,
    )
    again = rehearsal.build_manifest(
        source_table="chatroom-conversations",
        region="us-east-2",
        run_id="20260815-test",
        cutover_at_ms=1786752000000,
    )

    assert manifest == again
    assert manifest["metadata_table"].startswith(rehearsal.REHEARSAL_PREFIX)
    assert manifest["event_table"].endswith("-events")
    assert len(manifest["manifest_hash"]) == 64


def test_plan_stage_never_initializes_aws(monkeypatch, capsys):
    monkeypatch.setattr(
        rehearsal.boto3,
        "client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("plan must not initialize AWS")
        ),
    )

    assert rehearsal.main(_args("plan")) == 0
    assert "manifest_hash" in capsys.readouterr().out


def test_mutating_stage_requires_both_confirmations(monkeypatch, capsys):
    monkeypatch.setattr(
        rehearsal.boto3,
        "client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("confirmation must happen before AWS")
        ),
    )

    assert rehearsal.main(_args("prepare")) == 1
    assert "confirm-source-table" in capsys.readouterr().err


def test_manifest_rejects_non_live_source_and_invalid_run_id():
    for source, run_id in (("other-table", "valid"), ("chatroom-conversations", "INVALID")):
        try:
            rehearsal.build_manifest(
                source_table=source,
                region="us-east-2",
                run_id=run_id,
                cutover_at_ms=1,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid manifest input was accepted")
