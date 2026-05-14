import type { ChatMessage, InitOptions } from "./data/types";
import { ChatroomState } from "./data/state";
import {
  renderChatroom,
  appendBubble,
  appendSystemBubble,
  appendErrorBubble,
  updateTimerBar,
} from "./ui/renderer";
import { showPairingScreen } from "./ui/pairing";
import { formatTimerText } from "./ui/timer";
import { writeToED } from "./qualtrics/embedded-data";
import styles from "./ui/styles.css";

declare const $: JQueryStatic;
declare const jQuery: JQueryStatic;
const _$ = (typeof jQuery !== "undefined" ? jQuery : $) as JQueryStatic;

const state = new ChatroomState();

function injectStyles(): void {
  if (!document.getElementById("stim-chatroom-styles")) {
    const el = document.createElement("style");
    el.id = "stim-chatroom-styles";
    el.textContent = styles;
    document.head.appendChild(el);
  }
}

export async function init(options: InitOptions): Promise<void> {
  const $el = _$(options.element as any) as JQuery;

  // 1. Inject styles
  injectStyles();

  // Wire UI callbacks before init so they're ready
  state.onMessage((msg: ChatMessage, isSelf: boolean) => {
    appendBubble(msg.sender, msg.content, isSelf, msg.avatar?.emojiText);
    writeToED(state.getHistoryText());
  });

  state.onSystemEvent((content: string) => {
    appendSystemBubble(content);
    writeToED(state.getHistoryText());
  });

  state.onError((content: string) => {
    appendErrorBubble(content);
    writeToED(state.getHistoryText());
  });

  // 2. Exchange token
  try {
    await state.init(options);
  } catch {
    $el.html(
      `<div class="stim-error">Failed to connect to chatroom. Please check your chatroom ID and try again.</div>`
    );
    return;
  }

  const setting = state.chatroomSetting;

  // 3. Show pairing screen + prefetch messages in parallel
  const pairingSeconds = setting?.simulate_pairing_seconds || 0;
  if (pairingSeconds > 0) {
    // Start both in parallel: pairing animation + initial message fetch
    await Promise.all([
      showPairingScreen(options.element, pairingSeconds),
      state.prefetchEvents(),
    ]);
  } else {
    // No pairing screen — still prefetch so join events are ready
    await state.prefetchEvents();
  }

  // 4. Render chatroom UI
  renderChatroom(options.element, (text: string) => {
    state.send(text);
  });

  // 5. Replay prefetched events into the UI (join messages appear immediately)
  state.replayPrefetchedEvents();

  // 6. Start polling
  state.startPolling();

  // 7. Start timer if configured — show immediately then update every second
  const minMin = setting?.timer_min_minutes;
  const maxMin = setting?.timer_max_minutes;
  if (minMin || maxMin) {
    // Show initial text immediately (elapsed = 0)
    updateTimerBar(formatTimerText(0, minMin, maxMin));

    state.onTimerTick((elapsed: number) => {
      updateTimerBar(formatTimerText(elapsed, minMin, maxMin));
    });
    state.startTimer(minMin, maxMin);
  }
}

export function getHistory(): ChatMessage[] {
  return state.getHistory();
}

export function getHistoryText(): string {
  return state.getHistoryText();
}
