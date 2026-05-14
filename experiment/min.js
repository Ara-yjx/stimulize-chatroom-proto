import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import {
  BedrockRuntimeClient,
  ConverseCommand,
} from "@aws-sdk/client-bedrock-runtime";
import { PROMPT } from './prompt.js';

const client = new BedrockRuntimeClient({
  region: "us-east-2",
});

// Example only. Replace with a model ID you have access to in your region.
const MODEL_ID = "global.anthropic.claude-sonnet-4-6";

const rl = readline.createInterface({ input, output });

// Keep the whole conversation in memory
const messages = [];

async function sendMessage(userText) {
  messages.push({
    role: "user",
    content: [{ text: userText }],
  });

  const command = new ConverseCommand({
    modelId: MODEL_ID,
    messages,
    system: [{ text: PROMPT }],
    inferenceConfig: {
      maxTokens: 512,
      temperature: 0.7,
    },
  });

  const response = await client.send(command);

  const assistantText =
    response.output?.message?.content
      ?.filter((c) => c.text)
      .map((c) => c.text)
      .join("\n") || "(no text returned)";

  messages.push({
    role: "assistant",
    content: [{ text: assistantText }],
  });

  return assistantText;
}

console.log("Chatbot started. Type 'exit' to quit.\n");

while (true) {
  const userText = await rl.question("You: ");
  if (userText.trim().toLowerCase() === "exit") break;

  try {
    const reply = await sendMessage(userText);
    console.log(`\nBot: ${reply}\n`);
  } catch (err) {
    console.error("\nError:", err);
  }
}

rl.close();