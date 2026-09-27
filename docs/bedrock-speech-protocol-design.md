# Bedrock Speech Protocols

## TL;DR

Converse API support does not imply native tool calling, forced named-tool
selection, or schema-correct arguments. Select an explicit input/output protocol
for each tested model ID on our **bedrock-runtime / Converse / us-east-2** path:

- Native tools: send `toolConfig`, use a supported tool-choice mode, parse `toolUse`.
- JSON text: omit `toolConfig`, give JSON-only instructions/examples, parse and
  validate the final text ourselves. Do not silently reinterpret arbitrary prose.

Decision accepted. Local shared inference now selects auto tools for the tested
Llama 3.3/4 IDs and JSON for the tested Gemma 3, Llama 3.1 and R1 IDs. Platform
rules/examples, tick/batch triggers and exported prompt references follow that
selection; researcher text is preserved. Deployment and scoped cloned-room
acceptance are recorded in [the release worklog](./incident-fixes-20260928-worklog.md).
Do not equate the experimental probes with production reliability.

## Why We Investigated

A human-AI room produced repeated apparent silence. Gemma returned text resembling
`speak(messages: ["Hello"])`, not a `toolUse` block. Our old parser treated a
missing tool call as an empty message list; scheduling by last actual speech
then kept selecting the same AI. These are separate application bugs: output
errors must not masquerade as silence, and attempted turns must rotate fairly.

An AI-only incident also exposed the inherited 512-output-token budget. Empty
truncated tool arguments were misclassified as silence. The shared fix raises
the budget to 2048, records explicit output errors and usage, and accepts usable
truncated messages. Truncation does not justify inventing missing JSON fields.

## Gemma Investigation

We passed both API tool definitions and prompt instructions/examples. Bedrock
returned `text` containing pseudo-calls. Replacing ten pseudo-call examples with
JSON objects changed the output to valid JSON text in 2/2 trials, but produced no
native `toolUse`. Natural-language examples also produced only text. Minimal
tool-only instructions with named or auto choice produced empty content arrays
despite nonzero output usage. Sonnet with the same minimal tool request succeeded.

With identical minimal prompts, Gemma reported 57 input tokens without tools,
with the normal tool definition, and with an enlarged tool description. Sonnet
reported 749 versus 1749 tokens for normal/enlarged definitions. This is evidence
consistent with the tool configuration not being incorporated as expected on
this route, **not proof of Bedrock's internal implementation** or of every Gemma
endpoint's capabilities. Prompt examples demonstrably influence text formatting.

## Cross-Model Probe (2026-09-28)

23 exact editor model IDs: all 22 non-Anthropic options plus Sonnet 4.6 as a
control. Each gets named-tool speech; auto is tried if that fails; JSON text is
tested for speech and silence. Temperature 0.7, max output 512, SDK retries off.
No databases, chatroom settings, deployments, or customer conversations changed.

| Model IDs (prefix is part of the tested route) | Native tool result | JSON speech + silence |
| --- | --- | --- |
| `global.anthropic.claude-sonnet-4-6` | Named tool valid | Pass |
| `global.amazon.nova-2-lite-v1:0`; `us.amazon.nova-{pro,lite,micro}-v1:0` | Named tool valid | Pass |
| `us.amazon.nova-premier-v1:0` | API says model version reached end of life; not a tool-capability result | Not tested |
| `us.meta.llama4-{maverick,scout}-17b-instruct-v1:0`; `us.meta.llama3-3-70b-instruct-v1:0` | Named choice rejected; auto returns valid toolUse | Pass |
| `us.meta.llama3-1-70b-instruct-v1:0` | Named rejected; auto toolUse has messages as a JSON-encoded string, not an array | Pass |
| `us.meta.llama3-1-8b-instruct-v1:0` | Named rejected; auto returns pseudo-call text | Pass |
| `us.deepseek.r1-v1:0` | Named and auto explicitly rejected: model does not support tool use | Pass; reasoning adds output tokens |
| `deepseek.v3.2`; `deepseek.v3-v1:0` | Named tool returns valid schema; V3 did not reproduce the exact requested greeting | Pass |
| `qwen.qwen3-next-80b-a3b`; `qwen.qwen3-{235b-a22b-2507,32b}-v1:0` | Named tool valid | Pass |
| `google.gemma-3-{4b,12b,27b}-it` | Neither named nor auto produced toolUse | Pass; some JSON fenced |
| `mistral.mistral-large-3-675b-instruct`; `mistral.devstral-2-123b`; `mistral.ministral-3-14b-instruct` | Named tool valid | Pass; some JSON fenced |

Initial matrix: 76 API attempts; 44/44 JSON scenarios matched the expected
message array, after allowing a single enclosing JSON/unnamed Markdown fence.
Reported usage: 9,139 input and 1,400 output tokens. Short synthetic prompts,
not full conversation prompts. Other Claude versions were not retested here.

Follow-up: three more speech/silence pairs for each Gemma 3 variant, each Llama
3.1 variant, and R1: **36/36 passed**, four with enclosing fences. Usage: 2,508
input and 1,627 output tokens. R1 used 158..367 output tokens across the initial
and repeated tiny JSON scenarios, including reasoning overhead. We retain usage
but never display reasoning as chat content. Total new matrix/repeat attempts:
112; 11,647 input and 3,027 output tokens. This excludes earlier incident probes.

## Repeated Reliability Check (2026-09-28)

Acceptance target: at least 90% successful calls **per model**, not a pooled
average. Each baseline model received 30 calls: speech, explicit silence, and
forced speech, across five topics/history sizes (0..60 messages), twice each.
Temperature 0.7, output budget 2048, no SDK or application retries. Success means
valid schema and the requested speech/silence/message count, not literary quality.
These are synthetic protocol probes, not complete production prompts.

| Model | Protocol | Baseline | Stronger JSON instruction + new topics/history (1..80) |
| --- | --- | --- | --- |
| Gemma 3 4B | JSON | 28/30 (93.3%) | 30/30 (100%) |
| Gemma 3 12B | JSON | 30/30 (100%) | Not retested |
| Gemma 3 27B | JSON | 30/30 (100%) | Not retested |
| Llama 3.1 8B | JSON | 28/30 (93.3%) | **26/30 (86.7%)** |
| Llama 3.1 70B | JSON | **26/30 (86.7%)** | **26/30 (86.7%)** |
| DeepSeek R1 | JSON | 30/30 (100%) | Not retested |
| Llama 3.3 70B | Auto tools | 29/30 (96.7%) | Not retested |
| Llama 4 Scout | Auto tools | 30/30 (100%) | Not retested |
| Llama 4 Maverick | Auto tools | 28/30 (93.3%) | Not retested |

All 360 API calls succeeded at the transport/provider level. Failures were
schema failures; schema-valid responses all matched the requested behavior.
JSON failures mostly omitted the final `}` despite `end_turn`, not token
exhaustion; Gemma also returned a string instead of an array. Explicitly requiring
a complete closing brace and array did not reliably fix Llama 3.1. Auto failures:
Llama 3.3 returned text instead of a tool call once; Maverick emitted multiple
tool calls once and invalid arguments once. No bracket repair or prose fallback
was used to inflate results. Usage: 180,220 input and 13,722 output tokens.

Decision: Llama 3.1 JSON is **not ready** under this gate; retain both trial
results instead of choosing its better run. Other candidates meet the observed
overall threshold, but Gemma 4B baseline speech-forcing was only 8/10, so scenario
coverage matters too. Thirty samples cannot guarantee a true success rate above
90%; complete-prompt and cloned-room acceptance remains necessary. This run
does not authorize removing other models from the editor.

Only Nova Premier was removed from the maintained beta editor's selectable
models locally; other Nova options and existing room settings are unchanged.
No deployment or production protocol routing change was made in this check.

Subsequent local implementation: shared inference selects `auto` for Llama 3.3
70B and Llama 4 Scout/Maverick without altering the tool schema, usage accounting,
or error classification. A separate strict JSON parser rejects duplicate keys,
malformed envelopes and pseudo-calls. JSON inference omits toolConfig and uses
platform-only JSON rules/examples. Llama 3.1 remains an unstable selectable
model, not a claimed reliability pass. A later tick may recover from an output
error, but failures are not necessarily independent and still consume usage.

## Protocol Contract and Integration

Local integrated replay after routing changes: the original human-AI incident's
saved settings, personas and first human message produced valid speech in 9/9
calls (three each for its Llama 3.1 70B and two Gemma 3 4B participants). This uses
the full platform scaffold, not the short reliability prompts. It does not erase
the earlier Llama failures or prove long-run reliability. The original batch's
settings/personas produced valid Opus 4.6 speech in 2/2 calls, at 500 and 466
output tokens, without truncation. These direct local invocations did not create
conversations or write usage rows. Subsequent deployed clone and hosted browser
acceptance is recorded in the release worklog. Raw research content is retained
only in private local artifacts.

1. Use an explicit per-model capability map, normalizing only known geographic
   profile prefixes. Do not infer capability solely from provider name or cache
   support. Unknown/custom IDs require validation, not silent text fallback.
2. Candidate JSON text paths are the tested Gemma 3 variants and DeepSeek R1.
   Llama 3.1 needs further work: repeated tests failed the 90% gate above.
   Use auto tools for the tested Llama 4/3.3 variants. Preserve
   verified named-tool paths for the other tested models. An availability failure
   such as Nova Premier cannot be fixed by changing the prompt.
3. Render **platform-owned** output instructions and examples in the selected
   protocol before combining topic/persona/history. Do not rewrite researcher
   text with regex, append contradictory tool/JSON instructions, or change
   conversation behavior simply to change serialization. Human-AI, batch,
   forced-speech triggers and exported prompt references must agree.
4. JSON contract: exactly `{"messages": ["text"]}` or `{"messages": []}`.
   Accept one enclosing Markdown fence, then use a strict JSON parser and schema
   validation. Reject extra prose, pseudo-calls, wrong types, duplicate keys,
   invalid/empty strings, and invalid message counts. Never execute model text.
   Reasoning blocks are not chat messages.
5. Both protocols normalize to speech/silence/error, retaining provider usage,
   stop reason and protocol metadata. Only a valid empty array can mean silence;
   required-response turns reject it. Empty content or missing fields are errors.
   Never retry with a different model/protocol invisibly; retries remain bounded
   by the existing deadline and all calls retain usage records.
6. Usable text ending mid-sentence can succeed if the surrounding output is
   parseable. An incomplete JSON string/object is an explicit truncation error,
   not a guessed/repaired message. Existing 2048-token safety budget still applies.
7. Rollout gate: parser edge tests, complete human-AI/batch prompts, repeated
   original-model clones, silence/forced speech, usage/deadline checks, and preview
   diagnostics isolated from participant history/Qualtrics ED. Until then this
   table is diagnostic evidence, not a production-supported-model guarantee.

## Reproduce

From the repository root, with the backend venv active:

```bash
PYTHONPATH=backend python backend/scripts/probe_speech_protocols.py \
  --models google.gemma-3-4b-it us.meta.llama3-1-70b-instruct-v1:0 \
  --report .local/speech-protocol-probe.json
```

Default prints a plan only. Add `--invoke` for paid calls; use `--protocol json
--repeat 3` for repeated speech/silence controls. Reports stay local and contain
only synthetic probe inputs/outputs, usage and request metadata. There is no
database write. Region/model-specific results can change with provider updates.

For the repeated checks (omit `--invoke` to inspect the plan without paid calls):

```bash
PYTHONPATH=backend python backend/scripts/stress_speech_protocols.py --invoke \
  --report .local/protocol-stress-30.json
PYTHONPATH=backend python backend/scripts/stress_speech_protocols.py --invoke \
  --json-revision v2 --holdout --models google.gemma-3-4b-it \
  us.meta.llama3-1-8b-instruct-v1:0 us.meta.llama3-1-70b-instruct-v1:0 \
  --report .local/protocol-stress-v2-holdout.json
```

## Official References and Limits of Documentation

- [Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)
  exposes tools; API compatibility alone does not promise all optional features.
- [ToolChoice](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ToolChoice.html)
  documents restrictions on forcing a named tool. Our matrix also found named
  requests working for several other models; preserve measured route-specific
  evidence instead of turning the brief reference into a universal allowlist.
- [Gemma 3 4B model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-google-gemma-3-4b-it.html)
  lists client-side tool calling under bedrock-mantle, not an explicit guarantee
  for the Converse route tested here.
- [Model cards](https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html)
  now receive redirects from the former centralized Converse feature matrix.

No claim is made that Bedrock trains a model per request, that all Gemma/Llama
models lack tools, or that a successful short JSON probe guarantees schema
compliance during long or adversarial conversations.
