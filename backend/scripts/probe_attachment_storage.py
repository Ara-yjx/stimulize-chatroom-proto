#!/usr/bin/env python3
"""Isolated S3 -> attachment assembly -> Bedrock probe; never touches app data.

Creates a fresh private bucket, uploads the supplied JPEG, makes at most two
short paid inferences, and removes only resources created by this invocation.
No Lambda, Pages, database, IAM or existing bucket changes. Plan-only by default.
"""

import argparse
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

import boto3
from botocore.config import Config

from chatroom_api.prompt_attachments import (
    MANIFEST_FIELD, build_attachment_messages, check_serialized_request,
)
from chatroom_api.prompts.construction import (
    build_bedrock_cache_prefix_message, build_bedrock_system_blocks,
)
from chatroom_api.prompts.speech_scaffold import build_speak_tool_config, parse_speak_tool_call


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    raw = args.file.read_bytes()
    if not raw.startswith(b"\xff\xd8\xff") or not 0 < len(raw) <= 3_750_000:
        parser.error("Expected JPEG <=3.75 MB")
    asset_id = f"paid_{uuid4()}"
    key = f"assets/{asset_id}"
    bucket = f"stimulize-attachment-probe-{uuid4().hex[:20]}"
    model = "global.anthropic.claude-sonnet-4-6"
    report = {"status": "plan_only", "bucket": bucket, "runs": [],
              "cleanup": "not_created", "source_sha256": hashlib.sha256(raw).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(report, indent=2) + "\n")

    save()
    if not args.execute:
        print("Plan only: one new temporary bucket, two bounded Bedrock calls, cleanup.")
        return
    cfg = Config(connect_timeout=5, read_timeout=25, retries={"total_max_attempts": 1})
    s3 = boto3.client("s3", region_name="us-east-2", config=cfg)
    bedrock = boto3.client("bedrock-runtime", region_name="us-east-2", config=cfg)
    created = False
    try:
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": "us-east-2"})
        created = True
        s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True,
            "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
        s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": [
            {"Key": "Purpose", "Value": "isolated-attachment-probe"}]})
        s3.put_object(Bucket=bucket, Key=key, Body=raw, ServerSideEncryption="AES256")
        asset = {"id": asset_id, "format": "jpeg", "byte_size": len(raw),
                 "sha256": report["source_sha256"], "s3_key": key,
                 "width": args.width, "height": args.height}
        for humans in (1, 0):
            setting = {"human_count": humans, "ai_count": 2 if humans == 0 else 1,
                       "model_id": model, "mimic_human": False,
                       "prompt_attachment_ids": [asset_id], MANIFEST_FIELD: {asset_id: asset}}
            common = dict(mode="ai_only" if humans == 0 else "one_on_one",
                          chatroom_setting=setting, persona="", my_nickname="Reader",
                          history_block="(empty)", participant_nicknames=["Reader", "Visitor"],
                          require_response=True)
            prefix = build_bedrock_cache_prefix_message(**common)
            messages = build_attachment_messages([prefix], setting, {}, s3=s3,
                                                 bucket=bucket, supported_models={model})
            messages[0]["content"].append({"text": "Describe the objects in the picture, their colors and positions. Use speak, one message under 400 characters."})
            request = {"modelId": model,
                       "system": build_bedrock_system_blocks(**common, model_id=model),
                       "messages": messages,
                       "toolConfig": build_speak_tool_config(require_message=True, max_messages=1, max_message_chars=400),
                       "inferenceConfig": {"maxTokens": 300, "temperature": 0.7}}
            check_serialized_request(request, bedrock)
            started = time.monotonic()
            response = bedrock.converse(**request)
            parsed = parse_speak_tool_call(response)
            usage = response["usage"]
            cost = sum(usage.get(k, 0) * rate for k, rate in (
                ("inputTokens", 3), ("cacheWriteInputTokens", 3.75),
                ("cacheReadInputTokens", .30), ("outputTokens", 15))) / 1_000_000
            report["runs"].append({"human_count": humans, "usage": usage,
                                   "seconds": round(time.monotonic() - started, 2),
                                   "estimated_usd": cost, "messages": parsed})
            save()
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        raise
    finally:
        if created:
            s3.delete_object(Bucket=bucket, Key=key)
            s3.delete_bucket(Bucket=bucket)
            report["cleanup"] = "temporary object and bucket deleted"
        save()
    print(json.dumps({"status": report["status"], "runs": len(report["runs"]),
                      "estimated_usd": sum(r["estimated_usd"] for r in report["runs"]),
                      "cleanup": report["cleanup"]}))


if __name__ == "__main__":
    main()
