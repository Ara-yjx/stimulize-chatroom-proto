"""Execute the CDK ASL in Moto, with real worker/store code and fake model calls.

No AWS credentials, network services, RDS writes or paid inference are used.
Only the Lambda transport and Bedrock/usage boundary are replaced. DynamoDB
transactions and ASL Choice/Catch/Wait transitions run through Moto's engines.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError
from botocore.config import Config as BotoConfig
from moto import mock_aws
import pytest

from chatroom_api import config, event_store, bedrock_client
from chatroom_api.ai_batch import store, worker
from chatroom_api.ai_batch.contracts import conversation_id as batch_conversation_id


@pytest.fixture(scope="module")
def definition(request):
    root = Path(__file__).resolve().parents[2]
    node = os.environ.get("NODE_BINARY") or shutil.which("node")
    if not node:
        candidates = sorted((Path.home() / ".nvm/versions/node").glob("*/bin/node"))
        assert candidates, "Node required to load the actual CDK workflow definition"
        node = str(candidates[-1])
    function = getattr(request, "param", "conversationWorkflow")
    arguments = ("'arn:aws:lambda:us-east-2:123456789012:function:worker', "
                 if function == "conversationWorkflow" else "") + "'conversations', 'batches'"
    return json.loads(subprocess.check_output([
        node, "-r", "ts-node/register", "-e",
        f"console.log(JSON.stringify(require('./lib/ai-conversation-workflow').{function}({arguments})))",
    ], cwd=root / "cdk", text=True))


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")
    from moto.stepfunctions.parser.asl.component.state.choice.comparison.operator.implementations.is_operator import IsTimestamp
    # Moto 5.2.3 emits fractional +00:00 timestamps but its comparator only
    # parses whole-second Z strings. Supply RFC3339 parsing, not a fake clock.
    def timestamp(value):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
    monkeypatch.setattr(IsTimestamp, "string_to_timestamp", staticmethod(timestamp))
    with mock_aws(config={
        "stepfunctions": {"execute_state_machine": True},
        "core": {"passthrough": {"urls": [r"https://bedrock-runtime\.us-east-2\.amazonaws\.com/.*"]
                  if os.environ.get("AI_BATCH_LIVE_SMOKE") == "20" else []}},
    }):
        ddb = boto3.resource("dynamodb", region_name="us-east-2")
        for name, partition, sort in [
            ("batches", "batch_job_id", None), ("conversations", "conversation_id", None),
            ("events", "conversation_id", "event_key"),
        ]:
            keys = [{"AttributeName": partition, "KeyType": "HASH"}]
            if sort:
                keys.append({"AttributeName": sort, "KeyType": "RANGE"})
            ddb.create_table(TableName=name, KeySchema=keys, BillingMode="PAY_PER_REQUEST",
                             AttributeDefinitions=[{"AttributeName": k["AttributeName"], "AttributeType": "S"} for k in keys])
        for key, value in {"AI_BATCH_ENABLED": True, "AI_BATCH_TABLE": "batches",
                           "DYNAMODB_TABLE": "conversations", "DYNAMODB_EVENT_TABLE": "events",
                           "PROMPT_ATTACHMENTS_ENABLED": False}.items():
            monkeypatch.setattr(config, key, value)
        for module in (store, event_store):
            monkeypatch.setattr(module, "_client", boto3.client("dynamodb", region_name="us-east-2"))
            monkeypatch.setattr(module, "_metadata_table", ddb.Table("conversations"))
        monkeypatch.setattr(store, "_batch_table", ddb.Table("batches"))
        monkeypatch.setattr(event_store, "_event_table", ddb.Table("events"))
        now = int(time.time() * 1000)
        clock = [now]
        monkeypatch.setattr(store, "now_ms", lambda: clock[0])
        batch = {
            "batch_job_id": "batch", "owner_id": "9", "chatroom_id": "room", "status": "running",
            "deadline_at": now + 3600000, "batch_count": 1, "queued_count": 1, "running_count": 0,
            "unfinished_count": 1, "completed_count": 0, "failed_count": 0, "timed_out_count": 0,
            "settings_snapshot": {"model_id": "test-model", "temperature": "0.7", "mimic_human": True,
                                  "max_turns": 20, "max_total_chars": 20000, "max_message_chars": 400},
        }
        ddb.Table("batches").put_item(Item=batch)
        store.create_conversation({
            "conversation_id": "conversation", "batch_job_id": "batch", "conversation_type": "ai_batch",
            "status": "queued", "execution_state": "pending", "outcome": None,
            "deadline_at": batch["deadline_at"], "state_version": 0, "next_turn": 0,
            "message_count": 0, "total_chars": 0,
            "participants": [{"role": "ai", "ai_participant_id": f"ai-{i}", "nickname": f"AI {i}"} for i in range(2)],
        }, [], "initial")
        calls, usage = [], []

        def infer(*args, **kwargs):
            kwargs["before_attempt"]()
            calls.append(args)
            clock[0] += 10
            return {"messages": [f"Message {len(calls)}: considering the previous point."], "input_tokens": 10, "output_tokens": 5}

        monkeypatch.setattr(worker, "invoke_speak_tool", infer)
        monkeypatch.setattr(worker, "credits_allow", lambda _: True)
        monkeypatch.setattr(worker, "record_bedrock_usage", lambda **kwargs: usage.append(kwargs))
        yield SimpleNamespace(ddb=ddb, batch=batch, calls=calls, usage=usage, clock=clock, infer=infer)


def run_workflow(definition, runtime, monkeypatch, invoke=None, timeout_seconds=40, conversation_id="conversation"):
    from moto.stepfunctions.parser.asl.component.state.exec.state_task import lambda_eval_utils
    invocations = []

    def local_lambda(parameters, **kwargs):
        before = len(runtime.usage)
        context = SimpleNamespace(get_remaining_time_in_millis=lambda: 600000 if len(runtime.usage) - before < 5 else 0)
        invocations.append(parameters)
        try:
            body = json.loads(parameters["Payload"])
            result = invoke(body, context) if invoke else worker.lambda_handler(body, context)
            return {"StatusCode": 200, "Payload": result}
        except Exception as exc:
            return {"StatusCode": 200, "FunctionError": "Unhandled", "Payload": {"errorType": type(exc).__name__, "errorMessage": str(exc)}}

    monkeypatch.setattr(lambda_eval_utils, "_invoke_lambda_function", local_lambda)
    client = boto3.client("stepfunctions", region_name="us-east-2")
    machine = client.create_state_machine(name="test", definition=json.dumps(definition),
        roleArn="arn:aws:iam::123456789012:role/test")["stateMachineArn"]
    payload = {
        "batch_job_id": "batch", "conversation_id": conversation_id,
        "deadline": datetime.fromtimestamp(runtime.batch["deadline_at"] / 1000, timezone.utc).isoformat(),
    }
    if "RecoveryInput" in definition["States"]:
        payload = {"detail": {"status": "ABORTED", "input": json.dumps(payload)}}
    arn = client.start_execution(stateMachineArn=machine, name=uuid4().hex, input=json.dumps(payload))["executionArn"]
    until = time.monotonic() + timeout_seconds
    while time.monotonic() < until:
        result = client.describe_execution(executionArn=arn)
        if result["status"] != "RUNNING":
            assert result["status"] == "SUCCEEDED", client.get_execution_history(executionArn=arn)["events"][-8:]
            return invocations
        time.sleep(0.05)
    client.stop_execution(executionArn=arn)
    pytest.fail(f"local ASL execution did not finish within {timeout_seconds} seconds")


def test_twenty_messages_across_four_worker_slices(definition, runtime, monkeypatch):
    invocations = run_workflow(definition, runtime, monkeypatch)
    conv = store.get_conversation("conversation")
    batch = store.get_batch("batch")
    history = store.query_history("conversation")
    assert len(invocations) == 4
    assert len(runtime.calls) == len(runtime.usage) == len(history) == 20
    assert len({event["turn_write_id"] for event in history}) == 20
    assert all(a["ai_participant_id"] != b["ai_participant_id"] for a, b in zip(history, history[1:]))
    assert all(isinstance(event["timestamp"], (int,)) or int(event["timestamp"]) == event["timestamp"] for event in history)
    assert conv["message_count"] == conv["next_turn"] == 20
    assert conv["total_chars"] == sum(len(event["content"]) for event in history)
    assert (conv["execution_state"], conv["outcome"]) == ("terminal", "succeeded")
    assert batch["status"] == "completed" and batch["unfinished_count"] == batch["running_count"] == 0
    assert "worker_lease_id" not in conv


def test_late_result_discarded_but_usage_recorded(definition, runtime, monkeypatch):
    def late(*args, **kwargs):
        result = runtime.infer(*args, **kwargs)
        runtime.clock[0] = runtime.batch["deadline_at"]
        return result
    monkeypatch.setattr(worker, "invoke_speak_tool", late)
    run_workflow(definition, runtime, monkeypatch)
    assert len(runtime.calls) == len(runtime.usage) == 1
    assert store.query_history("conversation") == []
    assert store.get_conversation("conversation")["outcome"] == "timed_out"
    assert store.get_batch("batch")["status"] == "timed_out"


def test_worker_never_starts_native_finalizer_handles_failure(definition, runtime, monkeypatch):
    def broken(*args):
        raise RuntimeError("Lambda could not initialize")
    calls = run_workflow(definition, runtime, monkeypatch, broken)
    assert len(calls) == 3 and runtime.calls == []
    conv, batch = store.get_conversation("conversation"), store.get_batch("batch")
    assert conv["execution_state"] == "terminal" and conv["outcome"] == "failed"
    assert batch["status"] == "failed" and batch["failed_count"] == 1
    assert batch["unfinished_count"] == batch["queued_count"] == batch["running_count"] == 0


def test_committed_turn_then_crash_resumes_without_duplicate(definition, runtime, monkeypatch):
    commit = store.commit_turn
    crashed = []
    def commit_and_crash(*args, **kwargs):
        commit(*args, **kwargs)
        if not crashed:
            crashed.append(True)
            raise RuntimeError("lost Lambda response after committed turn")
    monkeypatch.setattr(store, "commit_turn", commit_and_crash)
    run_workflow(definition, runtime, monkeypatch)
    assert len(runtime.calls) == 20
    assert len(store.query_history("conversation")) == 20
    assert store.get_batch("batch")["completed_count"] == 1


def test_workflow_limit_is_not_message_count(definition, runtime, monkeypatch):
    definition = json.loads(json.dumps(definition))
    definition["States"]["CheckBounds"]["Choices"][1]["NumericGreaterThanEquals"] = 2
    calls = run_workflow(definition, runtime, monkeypatch)
    assert len(calls) == 2 and len(runtime.calls) == 10
    conv = store.get_conversation("conversation")
    assert conv["last_error"] == "worker_iteration_limit" and conv["outcome"] == "failed"


def test_deadline_before_start_never_invokes_worker(definition, runtime, monkeypatch):
    runtime.batch["deadline_at"] = int(time.time() * 1000) - 10000
    calls = run_workflow(definition, runtime, monkeypatch)
    assert calls == []
    assert store.get_conversation("conversation")["outcome"] == "timed_out"
    assert store.get_batch("batch")["timed_out_count"] == 1


def test_provider_retry_checks_deadline_before_each_attempt(runtime, monkeypatch):
    monkeypatch.setattr(bedrock_client.time, "sleep", lambda _: None)
    attempts = []
    def throttled():
        attempts.append(True)
        runtime.clock[0] = runtime.batch["deadline_at"]
        raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "retry"}}, "Converse")
    with pytest.raises(worker.ConversationDeadlineReached):
        bedrock_client._call_with_retry(throttled, lambda: worker._check_deadline(runtime.batch))
    assert len(attempts) == 1


@pytest.mark.parametrize("definition", ["conversationRecovery"], indirect=True)
def test_abort_native_recovery_does_not_invoke_lambda(definition, runtime, monkeypatch):
    assert run_workflow(definition, runtime, monkeypatch) == []
    assert store.get_conversation("conversation")["last_error"] == "workflow_ABORTED"
    assert store.get_batch("batch")["failed_count"] == 1
    # Duplicate/out-of-order events cannot decrement counters or overwrite outcome.
    assert run_workflow(definition, runtime, monkeypatch) == []
    assert store.get_batch("batch")["failed_count"] == 1
    assert store.get_batch("batch")["unfinished_count"] == 0


@pytest.mark.parametrize("definition", ["conversationRecovery"], indirect=True)
def test_recovery_preserves_already_committed_success(definition, runtime, monkeypatch):
    worker.lambda_handler({"batch_job_id": "batch", "conversation_id": "conversation"})
    assert run_workflow(definition, runtime, monkeypatch) == []
    assert store.get_conversation("conversation")["outcome"] == "succeeded"
    assert store.get_batch("batch")["completed_count"] == 1
    assert store.get_batch("batch")["failed_count"] == 0


def test_late_writer_cannot_append_after_terminal(runtime):
    store.start_conversation("batch", store.get_conversation("conversation"))
    stale = store.get_conversation("conversation")
    store.mark_conversation_terminal(stale, "timed_out")
    with pytest.raises(event_store.ConditionalWriteFailed):
        store.commit_turn(stale, {
            "type": "message", "role": "ai", "sender": "AI", "content": "late",
            "timestamp": runtime.clock[0], "turn_write_id": "late-write", "ai_participant_id": "ai-0",
        })
    assert store.query_history("conversation") == []
    assert store.get_batch("batch")["timed_out_count"] == 1


def test_usage_kept_and_no_correction_call_after_expiry(runtime, monkeypatch):
    def overlong(*args, **kwargs):
        result = runtime.infer(*args, **kwargs)
        result["messages"] = ["x" * 401]
        runtime.clock[0] = runtime.batch["deadline_at"]
        return result
    monkeypatch.setattr(worker, "invoke_speak_tool", overlong)
    worker.lambda_handler({"batch_job_id": "batch", "conversation_id": "conversation"})
    assert len(runtime.calls) == 1 and len(runtime.usage) == 1
    assert store.get_conversation("conversation")["outcome"] == "timed_out"


def test_atomic_failure_leaves_no_event_or_progress(runtime, monkeypatch):
    store.start_conversation("batch", store.get_conversation("conversation"))
    conv = store.get_conversation("conversation")
    runtime.ddb.Table("batches").update_item(Key={"batch_job_id": "batch"},
        UpdateExpression="SET unfinished_count = :zero", ExpressionAttributeValues={":zero": 0})
    with pytest.raises(event_store.ConditionalWriteFailed):
        store.commit_turn(conv, {"type": "message", "role": "ai", "content": "last", "timestamp": runtime.clock[0],
                                "ai_participant_id": "ai-0", "turn_write_id": "atomic-failure"}, terminal_status="completed")
    assert store.query_history("conversation") == []
    assert store.get_conversation("conversation")["message_count"] == 0


def test_one_failed_conversation_does_not_fail_other_conversations(definition, runtime, monkeypatch):
    second = {**store.get_conversation("conversation"), "conversation_id": "second"}
    runtime.ddb.Table("conversations").put_item(Item=second)
    runtime.ddb.Table("batches").update_item(Key={"batch_job_id": "batch"},
        UpdateExpression="SET batch_count = :two, queued_count = :two, unfinished_count = :two",
        ExpressionAttributeValues={":two": 2})
    run_workflow(definition, runtime, monkeypatch)
    assert store.get_batch("batch")["status"] == "running"
    assert store.get_batch("batch")["unfinished_count"] == 1
    def broken(*args):
        raise RuntimeError("second worker unavailable")
    run_workflow(definition, runtime, monkeypatch, broken, conversation_id="second")
    batch = store.get_batch("batch")
    assert batch["status"] == "partial_failure"
    assert batch["completed_count"] == batch["failed_count"] == 1
    assert batch["unfinished_count"] == batch["queued_count"] == batch["running_count"] == 0


def test_execution_dispatch_is_idempotent_after_lost_response(runtime, monkeypatch):
    arn = "arn:aws:states:us-east-2:123456789012:stateMachine:conversation-workflow"
    monkeypatch.setattr(config, "AI_BATCH_STATE_MACHINE_ARN", arn)
    requests = []
    def start(**kwargs):
        requests.append(kwargs)
        if len(requests) > 1:
            raise ClientError({"Error": {"Code": "ExecutionAlreadyExists", "Message": "already finished"}}, "StartExecution")
        return {}
    monkeypatch.setattr(store, "_sfn", SimpleNamespace(start_execution=start))
    expected = store.start_execution("batch", "conversation", runtime.batch["deadline_at"])
    assert store.start_execution("batch", "conversation", runtime.batch["deadline_at"]) == expected
    assert requests[0] == requests[1]
    assert store.get_conversation("conversation")["execution_arn"] == expected


def test_partial_provision_failure_preserves_completed_counter(runtime):
    first = batch_conversation_id("batch", 0)
    row = {**store.get_conversation("conversation"), "conversation_id": first}
    runtime.ddb.Table("conversations").put_item(Item=row)
    runtime.ddb.Table("conversations").delete_item(Key={"conversation_id": "conversation"})
    runtime.ddb.Table("batches").update_item(Key={"batch_job_id": "batch"},
        UpdateExpression="SET #s = :provisioning, created_at = :now, batch_count = :two, queued_count = :two, unfinished_count = :two, provision_lease_id = :lease",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":two": 2, ":provisioning": "provisioning", ":now": store.now_iso(), ":lease": "lease"})
    store.mark_conversation_terminal(row, "completed")
    assert store.fail_provision("batch", "lease", "could not dispatch second conversation")
    batch = store.get_batch("batch")
    assert batch["status"] == "partial_failure"
    assert batch["completed_count"] == batch["failed_count"] == 1
    assert batch["unfinished_count"] == batch["queued_count"] == batch["running_count"] == 0


@pytest.fixture
def live_client():
    if os.environ.get("AI_BATCH_LIVE_SMOKE") != "20":
        pytest.skip("Paid Bedrock smoke requires explicit AI_BATCH_LIVE_SMOKE=20")
    # Construct before Moto changes credentials. Only Bedrock is allowed through;
    # all metadata, history, workflows and usage storage remain local.
    return boto3.client("bedrock-runtime", region_name="us-east-2",
        config=BotoConfig(connect_timeout=5, read_timeout=90, retries={"total_max_attempts": 1}))


def test_live_twenty_message_conversation(live_client, definition, runtime, monkeypatch):
    setting = {**runtime.batch["settings_snapshot"],
               "model_id": "global.anthropic.claude-sonnet-4-6", "mimic_human": False,
               "max_message_chars": 400,
               "human_count": 0, "ai_count": 2,
               "topic_instruction": "Compare train and bicycle travel for a weekend trip. Discuss practical tradeoffs. Keep each reply under 100 characters."}
    runtime.ddb.Table("batches").update_item(Key={"batch_job_id": "batch"},
        UpdateExpression="SET settings_snapshot = :setting", ExpressionAttributeValues={":setting": setting})
    monkeypatch.setattr(bedrock_client, "_deadline_client", live_client)

    def infer(*args, **kwargs):
        kwargs["before_attempt"]()
        if len(runtime.calls) >= 24:
            raise worker.TerminalConversationError("local smoke inference budget exhausted")
        runtime.calls.append(args)
        response = bedrock_client.invoke_speak_tool(*args, **kwargs)
        runtime.clock[0] = int(time.time() * 1000)
        return response

    monkeypatch.setattr(worker, "invoke_speak_tool", infer)
    # Real inference may take several minutes; each five-call slice still tests
    # immediate continuation. No production tables or usage ledger are written.
    calls = run_workflow(definition, runtime, monkeypatch, timeout_seconds=600)
    history = store.query_history("conversation")
    output = os.environ.get("AI_BATCH_SMOKE_OUTPUT")
    if output:
        folder = Path(output)
        folder.mkdir(parents=True, exist_ok=True)
        encode = lambda value: int(value) if value == int(value) else float(value)
        (folder / "conversation.json").write_text(json.dumps(history, default=encode, indent=2), encoding="utf-8")
        (folder / "conversation.txt").write_text("\n\n".join(f"{event['sender']}: {event['content']}" for event in history), encoding="utf-8")
        totals = {field: sum(int(item["result"].get(field, 0)) for item in runtime.usage) for field in
                  ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_write_input_tokens")}
        (folder / "summary.json").write_text(json.dumps({"messages": len(history), "worker_slices": len(calls),
            "inferences": len(runtime.calls), "usage": totals,
            "outcome": store.get_conversation("conversation")["outcome"]}, indent=2), encoding="utf-8")
    assert store.get_conversation("conversation")["outcome"] == "succeeded", store.get_conversation("conversation").get("last_error")
    assert len(history) == 20 and len(calls) >= 4
