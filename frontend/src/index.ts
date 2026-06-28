import type { ChatMessage, InitOptions } from "./data/types";
import { ChatroomState } from "./data/state";
import {
  renderChatroom,
  appendBubble,
  appendSystemBubble,
  appendErrorBubble,
  updateTimerBar,
} from "./ui/renderer";
import { renderPairingScreen, showLobbyAborted } from "./ui/pairing";
import { showConversationEnded } from "./ui/ended";
import { showReconnectBanner, hideReconnectBanner } from "./ui/reconnect";
import { formatTimerText } from "./ui/timer";
import { writeToED } from "./qualtrics/embedded-data";
import { isQualtricsMobilePreview } from "./qualtrics/environment";
import styles from "./ui/styles.css";

declare const $: JQueryStatic;
declare const jQuery: JQueryStatic;
const _$ = (typeof jQuery !== "undefined" ? jQuery : $) as JQueryStatic;
const DEFAULT_API_BASE_URL = "https://pmvb4orly5.execute-api.us-east-2.amazonaws.com/prod";

let state: ChatroomState | null = null;

function resolveMountElement(options: InitOptions): HTMLElement {
  if (options.element) {
    if (typeof options.element === "string") {
      const existing = document.querySelector(options.element);
      if (!(existing instanceof HTMLElement)) {
        throw new Error(`Chatroom element not found: ${options.element}`);
      }
      return existing;
    }
    return options.element;
  }

  if (!options.parentElement) {
    throw new Error("StimulizeChatroom.init requires element or parentElement");
  }

  const parent =
    typeof options.parentElement === "string"
      ? document.querySelector(options.parentElement)
      : options.parentElement;
  if (!(parent instanceof HTMLElement)) {
    throw new Error("Chatroom parentElement not found");
  }

  const child = document.createElement("div");
  if (options.elementStyle) {
    Object.assign(child.style, options.elementStyle);
  }
  parent.appendChild(child);
  return child;
}

function showMobilePreviewDisabledMessage(element: HTMLElement): void {
  element.innerHTML = `
    <div class="stim-chatroom">
      <div class="stim-error">Please use the Qualtrics desktop preview to avoid data collision issue</div>
    </div>
  `;
}

function injectStyles(): void {
  if (!document.getElementById("stim-chatroom-styles")) {
    const el = document.createElement("style");
    el.id = "stim-chatroom-styles";
    el.textContent = styles;
    document.head.appendChild(el);
  }
}

function showBetaUrlInput(element: string | HTMLElement, options: InitOptions): Promise<string> {
  const $el = _$(element as any) as JQuery;
  const defaultUrl = options.apiBaseUrl || DEFAULT_API_BASE_URL;
  $el.html(`
    <div class="stim-chatroom">
      <div class="stim-beta-config">
        <label class="stim-beta-label">API Base URL (Beta)</label>
        <input type="text" class="stim-beta-input" value="${defaultUrl}" />
        <button class="stim-beta-start">Start Chat</button>
      </div>
    </div>
  `);
  return new Promise((resolve) => {
    $el.find(".stim-beta-start").on("click", () => {
      const url = ($el.find(".stim-beta-input").val() as string || "").trim();
      resolve(url || defaultUrl);
    });
  });
}

export async function init(options: InitOptions): Promise<void> {
  // Guard against multi-mount
  if (state) {
    state.destroy();
  }
  state = new ChatroomState();

  const element = resolveMountElement(options);
  const resolvedOptions: InitOptions = { ...options, element };
  const $el = _$(element as any) as JQuery;

  // 1. Inject styles
  injectStyles();

  if (isQualtricsMobilePreview()) {
    showMobilePreviewDisabledMessage(element);
    return;
  }

  // 2. Beta mode: show URL input before anything else
  if (options.beta) {
    const url = await showBetaUrlInput(element, options);
    options = { ...resolvedOptions, apiBaseUrl: url };
  } else {
    options = resolvedOptions;
  }

  // Wire UI callbacks before init so they're ready
  state.onMessage((msg: ChatMessage, isSelf: boolean) => {
    appendBubble(msg.sender, msg.content, isSelf, msg.avatar?.emojiText);
    writeToED(state!.getHistory(), state!.getHistoryText());
  });

  state.onSystemEvent((content: string) => {
    appendSystemBubble(content);
    writeToED(state!.getHistory(), state!.getHistoryText());
  });

  state.onError((content: string) => {
    appendErrorBubble(content);
    writeToED(state!.getHistory(), state!.getHistoryText());
  });

  state.onConversationEnded(() => {
    appendSystemBubble("This conversation has ended.");
    showConversationEnded(element);
    writeToED(state!.getHistory(), state!.getHistoryText());
  });

  state.onLobbyAborted(() => {
    showLobbyAborted(element, () => {
      init(state!.getInitOptions()!);
    });
  });

  state.onReconnecting((reconnecting: boolean) => {
    if (reconnecting) {
      showReconnectBanner(element);
    } else {
      hideReconnectBanner();
    }
  });

  // 3. Exchange token
  try {
    await state.init(options);
  } catch {
    $el.html(
      `<div class="stim-error">Failed to connect to chatroom. Please check your chatroom ID and try again.</div>`
    );
    return;
  }

  // 4. Pre-chat-UI phase: server-managed lobby wait.
  //
  // The widget no longer sleeps locally for simulate_pairing_seconds. The
  // server owns all pairing waits: multi-human waits use max_wait_seconds;
  // one-human mimic-human simulated waits use simulate_pairing_seconds as the
  // lobby deadline. The widget simply keeps the pairing screen up while
  // /chat/messages returns a lobby block.
  await state.prefetchEvents();
  if (state.isInLobby()) {
    renderPairingScreen(element);

    // Use the dedicated lobby-poll loop (not startPolling) so events that
    // land at lobby-close don't get dispatched into the still-pairing-screen
    // DOM. After it closes, re-prefetch from the top so the rendered chat
    // includes joins + system events from the start.
    try {
      await state.pollLobbyUntilClosed();
    } catch (err) {
      if (err instanceof Error && err.message === "lobby_aborted") {
        // onLobbyAborted (registered above) has already swapped the DOM to
        // the abort screen. Bail before rendering chat UI.
        return;
      }
      throw err;
    }
    // Lobby closed: re-fetch the now-visible conversation history.
    await state.prefetchEvents();
  }

  // 5. Render chatroom UI
  renderChatroom(element, (text: string) => {
    state!.send(text);
  });

  // 6. Replay prefetched events into the UI
  state.replayPrefetchedEvents();

  // 7. Start polling
  state.startPolling();

  // 8. Start timer if configured
  const minMin = state.chatroomSetting?.timer_min_minutes;
  const maxMin = state.chatroomSetting?.timer_max_minutes;
  if (minMin || maxMin) {
    updateTimerBar(formatTimerText(0, minMin, maxMin));

    state.onTimerTick((elapsed: number) => {
      updateTimerBar(formatTimerText(elapsed, minMin, maxMin));
    });
    state.startTimer(minMin, maxMin);
  }
}

export function getHistory(): ChatMessage[] {
  return state ? state.getHistory() : [];
}

export function getHistoryText(): string {
  return state ? state.getHistoryText() : "";
}
