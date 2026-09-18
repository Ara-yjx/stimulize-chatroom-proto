#!/usr/bin/env python3
"""Bounded, opt-in Bedrock PDF/image probe; no chatroom/usage DB or cloud writes.

Run from backend with PYTHONPATH=. and boto3 installed. Output contains model
answers: keep it private. Pricing is an estimate for global Sonnet 4.6 only.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from chatroom_api.prompts.construction import (
    build_bedrock_cache_prefix_message, build_bedrock_system_blocks,
)
from chatroom_api.prompts.speech_scaffold import build_speak_tool_config, parse_speak_tool_call


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="PDF, PNG or JPEG")
    parser.add_argument("--question", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--citations", choices=["on", "off"], default="on")
    parser.add_argument("--repeats", type=int, choices=[1, 2], default=2)
    parser.add_argument("--invoke", action="store_true", help="Authorize up to two billed inferences")
    args = parser.parse_args()
    raw = args.file.read_bytes()
    if raw.startswith(b"%PDF-") and len(raw) <= 4_500_000:
        file_format = "pdf"
        attachment = {"document": {
            "format": "pdf", "name": "Reference document", "source": {"bytes": raw},
            "citations": {"enabled": args.citations == "on"},
        }}
    elif len(raw) <= 3_750_000 and (raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"\x89PNG\r\n\x1a\n")):
        file_format = "jpeg" if raw.startswith(b"\xff\xd8\xff") else "png"
        attachment = {"image": {"format": file_format, "source": {"bytes": raw}}}
    else:
        parser.error("Expected PDF <=4.5 MB or PNG/JPEG <=3.75 MB; probe does not convert files")
    model = "global.anthropic.claude-sonnet-4-6"
    setting = {"human_count": 0, "ai_count": 2, "mimic_human": True,
               "topic_instruction": "Discuss the attached document accurately; do not invent unreadable details."}
    common = dict(mode="ai_only", chatroom_setting=setting, persona="",
                  my_nickname="Reader", history_block="(empty)",
                  participant_nicknames=["Reader", "Researcher"], require_response=True)
    system = build_bedrock_system_blocks(**common, model_id=model)
    prefix = build_bedrock_cache_prefix_message(**common)
    checkpoint = next(i for i, block in enumerate(prefix["content"]) if "cachePoint" in block)
    prefix["content"].insert(checkpoint, attachment)
    prefix["content"].append({"text": args.question +
        " Answer through speak in one concise message, at most 400 characters. If you cannot see a detail, say so."})
    request = dict(modelId=model, system=system, messages=[prefix],
                   toolConfig=build_speak_tool_config(require_message=True, max_message_chars=400, max_messages=1),
                   inferenceConfig={"maxTokens": 512, "temperature": 0.7})
    report = {"file": args.file.name, "format": file_format, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
              "model": model, "region": args.region, "citations": args.citations if file_format == "pdf" else "not_applicable",
              "question": args.question, "runs": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    if not args.invoke:
        report["status"] = "plan_only; add --invoke for billed inference"
        save()
        print(json.dumps(report, ensure_ascii=False))
        return
    client = boto3.client("bedrock-runtime", region_name=args.region,
                          config=Config(connect_timeout=10, read_timeout=120,
                                        retries={"total_max_attempts": 1}))
    try:
        report["count_tokens"] = client.count_tokens(
            modelId=model, input={"converse": {"system": system, "messages": [prefix]}})
    except ClientError as exc:
        report["count_tokens_error"] = exc.response["Error"]
    save()
    for number in range(args.repeats):
        start = time.monotonic()
        try:
            response = client.converse(**request)
            usage = response["usage"]
            cost = (usage.get("inputTokens", 0)*3 + usage.get("cacheWriteInputTokens", 0)*3.75
                    + usage.get("cacheReadInputTokens", 0)*0.30 + usage.get("outputTokens", 0)*15)/1e6
            result = {"run": number + 1, "seconds": round(time.monotonic()-start, 2),
                      "usage": usage, "estimated_usd": round(cost, 8),
                      "stop_reason": response.get("stopReason"),
                      "output": response["output"],
                      "request_id": response.get("ResponseMetadata", {}).get("RequestId")}
            try:
                result["parsed_messages"] = parse_speak_tool_call(response)
            except Exception as exc:
                result["parse_error"] = str(exc)
            report["runs"].append(result)
        except ClientError as exc:
            report["runs"].append({"run": number + 1, "error": exc.response["Error"]})
            save()
            break
        save()
    report["estimated_total_usd"] = round(sum(r.get("estimated_usd", 0) for r in report["runs"]), 8)
    save()
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
