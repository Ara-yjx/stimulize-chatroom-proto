// =============================================================================
// Group-mode tick POC
//
// Simulates a 1 human + 2 AI conversation with virtual time. Verifies:
//   - the "tick" mechanism (gate + AI decision-to-speak via Bedrock tool use)
//   - the prompt composition (SCAFFOLD + TOPIC + <your-name> + <history>)
//   - the `speak` tool returning empty array for silence
//
// Console commands:
//   \s some text     send a message as the human
//   \t               run a tick — gate picks an AI candidate
//   \t1              force-tick AI-1 (skips gate)
//   \t2              force-tick AI-2 (skips gate)
//   \p               dump the most recent prompt (system + history) sent to Bedrock
//   \h               print the simulated event history
//   \q               quit
//
// Time is virtual. Each command advances the clock by 5 simulated seconds.
// =============================================================================

import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import {
  BedrockRuntimeClient,
  ConverseCommand,
} from "@aws-sdk/client-bedrock-runtime";
import { SPEECH_SCAFFOLD, CHATROOM_TOPIC_INSTRUCTION } from "./group-mode-prompt.js";

// -----------------------------------------------------------------------------
// Config
// -----------------------------------------------------------------------------

const MODEL_ID = "global.anthropic.claude-sonnet-4-6";
const REGION = "us-east-2";
const TICK_SECONDS = 5;            // virtual seconds advanced per command
const GATE_MIN_SILENCE_MS = 5000;  // gate: skip if last msg less than this ago
const GATE_AI_COOLDOWN_MS = 5000;  // gate: skip if same AI just spoke
const TYPING_DELAY_MIN_MS = 2000;  // simulated typing delay range (per AI message)
const TYPING_DELAY_MAX_MS = 8000;
const HUMAN = { nickname: "Earth" };
const AIS = [
  { id: 1, nickname: "Mars" },
  { id: 2, nickname: "Venus" },
];

// -----------------------------------------------------------------------------
// State
// -----------------------------------------------------------------------------

let virtualNowMs = 0;          // virtual conversation clock
const events = [];             // [{ type, sender, content, timestamp }]
                               // type: "system" | "message" | "tick" (display-only)
let lastPrompt = null;          // last (system, messages) pair sent to Bedrock

// -----------------------------------------------------------------------------
// Bedrock client + speak tool
// -----------------------------------------------------------------------------

const client = new BedrockRuntimeClient({ region: REGION });

const SPEAK_TOOL = {
  toolSpec: {
    name: "speak",
    description:
      "Decide what messages (if any) to send right now. Pass an empty array to stay silent.",
    inputSchema: {
      json: {
        type: "object",
        properties: {
          messages: {
            type: "array",
            items: { type: "string" },
            description:
              "Zero or more message texts. Multiple items become multiple bubbles.",
          },
        },
        required: ["messages"],
      },
    },
  },
};

// -----------------------------------------------------------------------------
// Time helpers
// -----------------------------------------------------------------------------

function advanceClock() {
  virtualNowMs += TICK_SECONDS * 1000;
}

function fmtAgo(ms) {
  const sec = Math.max(0, Math.round((virtualNowMs - ms) / 1000));
  return `${sec} sec ago`;
}

// -----------------------------------------------------------------------------
// Event helpers
// -----------------------------------------------------------------------------

function pushSystem(content) {
  events.push({
    type: "system",
    sender: "System",
    content,
    timestamp: virtualNowMs,
    visible_at: virtualNowMs,
  });
}

function pushMessage(sender, content, visibleAt = null) {
  events.push({
    type: "message",
    sender,
    content,
    timestamp: virtualNowMs,
    visible_at: visibleAt ?? virtualNowMs,
  });
}

function visibleEvents() {
  return events.filter((e) => e.visible_at <= virtualNowMs);
}

function lastVisibleMessageEvent() {
  const v = visibleEvents();
  for (let i = v.length - 1; i >= 0; i--) {
    if (v[i].type === "message") return v[i];
  }
  return null;
}

function lastVisibleMessageBy(sender) {
  const v = visibleEvents();
  for (let i = v.length - 1; i >= 0; i--) {
    if (v[i].type === "message" && v[i].sender === sender) return v[i];
  }
  return null;
}

// Latest visible_at across an AI's most recent batch of authored messages.
// "Still typing" until that timestamp passes.
function aiStillTyping(nickname) {
  // find the last batch authored by this AI (consecutive messages with same timestamp)
  let lastTs = -1;
  let maxVisibleAt = -1;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.type !== "message" || e.sender !== nickname) continue;
    if (lastTs < 0) lastTs = e.timestamp;
    if (e.timestamp !== lastTs) break;
    if (e.visible_at > maxVisibleAt) maxVisibleAt = e.visible_at;
  }
  return maxVisibleAt > virtualNowMs;
}

// -----------------------------------------------------------------------------
// Gate (server-side decision: should we even invoke Bedrock for any AI?)
// -----------------------------------------------------------------------------

function runGate() {
  const last = lastVisibleMessageEvent();

  if (last && virtualNowMs - last.visible_at < GATE_MIN_SILENCE_MS) {
    return {
      skip: true,
      reason: `min_silence_not_elapsed (${(virtualNowMs - last.visible_at) / 1000}s < 5s)`,
    };
  }

  // Pick the AI that has been silent longest (visible-only).
  // Skip any AI that's still typing its previous turn.
  const scored = AIS
    .filter((ai) => !aiStillTyping(ai.nickname))
    .map((ai) => {
      const lastSpoke = lastVisibleMessageBy(ai.nickname);
      return {
        ai,
        lastSpokeMs: lastSpoke ? lastSpoke.visible_at : -1,
      };
    });

  if (scored.length === 0) {
    return { skip: true, reason: "all AIs still typing" };
  }

  scored.sort((a, b) => a.lastSpokeMs - b.lastSpokeMs || a.ai.id - b.ai.id);
  const candidate = scored[0].ai;

  // Cooldown check on the candidate
  const candidateLast = lastVisibleMessageBy(candidate.nickname);
  if (
    candidateLast &&
    virtualNowMs - candidateLast.visible_at < GATE_AI_COOLDOWN_MS
  ) {
    return {
      skip: true,
      reason: `ai_just_spoke (${candidate.nickname}, ${
        (virtualNowMs - candidateLast.visible_at) / 1000
      }s < 5s)`,
    };
  }

  return { skip: false, candidate };
}

// -----------------------------------------------------------------------------
// Prompt composition (per-AI, per-tick)
//
// Each AI sees:
//   <system>
//     SPEECH_SCAFFOLD
//     CHATROOM_TOPIC_INSTRUCTION
//     <your-name>...</your-name>
//     <conversation-history>...</conversation-history>
//   </system>
//
// Each AI does NOT know who else is AI. All other participants appear by
// nickname only. The AI knows its own nickname via <your-name>.
//
// Conversation history mapping (from LLD):
//   - own messages  -> assistant role
//   - everything else (humans + other AIs, by nickname only) -> user role
//   - tick-only events are excluded
//   - system events (e.g. "Chatroom created") are also excluded from the
//     Bedrock messages array; we render them in the textual <conversation-history>
//     block at the start of the system prompt instead.
//
// For the POC, to keep things readable, we put the entire history as a single
// rendered text block inside the system prompt and send a final dummy user
// message asking the AI to make its decision. This mirrors how the production
// handler will likely work (full re-render every tick).
// -----------------------------------------------------------------------------

function renderHistoryBlock() {
  const lines = visibleEvents()
    .filter((e) => e.type !== "tick")
    .map((e) => {
      const ago = fmtAgo(e.visible_at);
      if (e.type === "system") return `> [${ago}] System: ${e.content}`;
      return `> [${ago}] ${e.sender}: ${e.content}`;
    });
  return lines.join("\n");
}

function buildPromptFor(ai) {
  const historyBlock = renderHistoryBlock();

  const systemText =
    SPEECH_SCAFFOLD +
    "\n" +
    CHATROOM_TOPIC_INSTRUCTION +
    "\n\n" +
    `<your-name>\n${ai.nickname}\n</your-name>\n\n` +
    `<conversation-history>\n${historyBlock || "(empty)"}\n</conversation-history>`;

  const userTrigger =
    "Based on the conversation above, decide whether to speak. Always call the `speak` tool. If you choose silence, call it with an empty messages array.";

  return {
    system: [{ text: systemText }],
    messages: [{ role: "user", content: [{ text: userTrigger }] }],
  };
}

// -----------------------------------------------------------------------------
// Bedrock invocation
// -----------------------------------------------------------------------------

async function invokeAi(ai) {
  const prompt = buildPromptFor(ai);
  lastPrompt = { ai: ai.nickname, ...prompt };

  const command = new ConverseCommand({
    modelId: MODEL_ID,
    system: prompt.system,
    messages: prompt.messages,
    toolConfig: {
      tools: [SPEAK_TOOL],
      toolChoice: { tool: { name: "speak" } },
    },
    inferenceConfig: { maxTokens: 512, temperature: 0.7 },
  });

  const response = await client.send(command);

  const blocks = response.output?.message?.content || [];
  const toolUse = blocks.find((b) => b.toolUse)?.toolUse;
  if (!toolUse) {
    return { messages: [], raw: blocks, error: "no toolUse block in response" };
  }
  const messages = Array.isArray(toolUse.input?.messages)
    ? toolUse.input.messages
    : [];

  const usage = response.usage || {};
  return {
    messages,
    inputTokens: usage.inputTokens,
    outputTokens: usage.outputTokens,
    raw: toolUse,
  };
}

// -----------------------------------------------------------------------------
// Tick handling
// -----------------------------------------------------------------------------

async function tickAuto() {
  advanceClock();
  const gate = runGate();
  if (gate.skip) {
    console.log(`\n[tick @ t=${virtualNowMs / 1000}s] GATE SKIP: ${gate.reason}\n`);
    return;
  }
  console.log(
    `\n[tick @ t=${virtualNowMs / 1000}s] GATE PASS -> candidate: ${gate.candidate.nickname}`,
  );
  await runAi(gate.candidate);
}

async function tickForce(aiId) {
  advanceClock();
  const ai = AIS.find((a) => a.id === aiId);
  if (!ai) {
    console.log(`\nNo AI with id ${aiId}\n`);
    return;
  }
  console.log(
    `\n[tick @ t=${virtualNowMs / 1000}s] FORCE -> ${ai.nickname} (gate skipped)`,
  );
  await runAi(ai);
}

function pickTypingDelayMs() {
  return TYPING_DELAY_MIN_MS +
    Math.floor(Math.random() * (TYPING_DELAY_MAX_MS - TYPING_DELAY_MIN_MS + 1));
}

async function runAi(ai) {
  let result;
  try {
    result = await invokeAi(ai);
  } catch (err) {
    console.error("Bedrock error:", err.name || err.message, err.message);
    return;
  }
  const { messages, inputTokens, outputTokens } = result;
  if (messages.length === 0) {
    console.log(
      `  ${ai.nickname} stayed silent. (in=${inputTokens}, out=${outputTokens})\n`,
    );
    return;
  }
  // Stack typing delays across the multi-message turn.
  let visibleAt = virtualNowMs;
  for (const m of messages) {
    visibleAt += pickTypingDelayMs();
    pushMessage(ai.nickname, m, visibleAt);
    const offset = (visibleAt - virtualNowMs) / 1000;
    console.log(`  ${ai.nickname} [+${offset.toFixed(1)}s]: ${m}`);
  }
  console.log(`  (in=${inputTokens}, out=${outputTokens})\n`);
}

// -----------------------------------------------------------------------------
// Console
// -----------------------------------------------------------------------------

function dumpHistory() {
  console.log(`\n--- History (t=${virtualNowMs / 1000}s) ---`);
  for (const e of events) {
    const visAgo = fmtAgo(e.visible_at);
    const pending = e.visible_at > virtualNowMs
      ? ` (pending, visible in +${((e.visible_at - virtualNowMs) / 1000).toFixed(1)}s)`
      : "";
    if (e.type === "system") {
      console.log(`  [${visAgo}] System: ${e.content}${pending}`);
    } else {
      console.log(`  [${visAgo}] ${e.sender}: ${e.content}${pending}`);
    }
  }
  console.log("------\n");
}

function dumpPrompt() {
  if (!lastPrompt) {
    console.log("\n(no prompt sent yet)\n");
    return;
  }
  console.log(`\n--- Last prompt (for ${lastPrompt.ai}) ---`);
  console.log("\n[system]\n");
  console.log(lastPrompt.system[0].text);
  console.log("\n[messages]\n");
  console.log(JSON.stringify(lastPrompt.messages, null, 2));
  console.log("\n--- end ---\n");
}

function printHelp() {
  console.log(`
commands:
  \\s <text>   send a message as the human (you)
  \\t          tick — gate picks one AI candidate
  \\t1, \\t2    force-tick AI-1 (Mars) / AI-2 (Venus), skip gate
  \\p          dump the last prompt sent to Bedrock
  \\h          print event history
  \\?          this help
  \\q          quit
`);
}

// -----------------------------------------------------------------------------
// Main loop
// -----------------------------------------------------------------------------

async function main() {
  pushSystem("Chatroom created.");
  console.log(
    `Group POC — 1 human (${HUMAN.nickname}) + 2 AIs (${AIS.map((a) => a.nickname).join(
      ", ",
    )})`,
  );
  console.log(`Topic: anything about your college life.`);
  console.log(`Model: ${MODEL_ID} in ${REGION}.`);
  printHelp();

  const rl = readline.createInterface({ input, output });

  while (true) {
    const line = (await rl.question(`[t=${virtualNowMs / 1000}s] > `)).trim();
    if (!line) continue;

    if (line === "\\q") break;
    if (line === "\\?") { printHelp(); continue; }
    if (line === "\\h") { dumpHistory(); continue; }
    if (line === "\\p") { dumpPrompt(); continue; }
    if (line === "\\t") { await tickAuto(); continue; }
    if (line === "\\t1") { await tickForce(1); continue; }
    if (line === "\\t2") { await tickForce(2); continue; }

    if (line.startsWith("\\s ")) {
      advanceClock();
      const text = line.slice(3);
      pushMessage(HUMAN.nickname, text);
      console.log(`  ${HUMAN.nickname}: ${text}\n`);
      continue;
    }

    console.log(`unknown command. type \\? for help`);
  }

  rl.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
