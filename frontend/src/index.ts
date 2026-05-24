import type { ChatMessage, InitOptions } from "./data/types";
import { ChatroomState } from "./data/state";
import {
  renderChatroom,
  appendBubble,
  appendSystemBubble,
  appendErrorBubble,
  updateTimerBar,
} from "./ui/renderer";
import { showPairingScreen, renderPairingScreen, showLobbyAborted } from "./ui/pairing";
import { showConversationEnded } from "./ui/ended";
import { showReconnectBanner, hideReconnectBanner } from "./ui/reconnect";
import { formatTimerText } from "./ui/timer";
import { writeToED } from "./qualtrics/embedded-data";
import styles from "./ui/styles.css";

declare const $: JQueryStatic;
declare const jQuery: JQueryStatic;
const _$ = (typeof jQuery !== "undefined" ? jQuery : $) as JQueryStatic;
const DEFAULT_API_BASE_URL = "https://pmvb4orly5.execute-api.us-east-2.amazonaws.com/prod";

let state: ChatroomState | null = null;

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

  const $el = _$(options.element as any) as JQuery;

  // 1. Inject styles
  injectStyles();

  // 2. Beta mode: show URL input before anything else
  if (options.beta) {
    const url = await showBetaUrlInput(options.element, options);
    options = { ...options, apiBaseUrl: url };
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
    showConversationEnded(options.element);
    writeToED(state!.getHistory(), state!.getHistoryText());
  });

  state.onLobbyAborted(() => {
    showLobbyAborted(options.element, () => {
      init(state!.getInitOptions()!);
    });
  });

  state.onReconnecting((reconnecting: boolean) => {
    if (reconnecting) {
      showReconnectBanner(options.element);
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

  const setting = state.chatroomSetting;

  // 4. Pre-chat-UI phase: pairing screen + lobby wait.
  //
  // Three cases:
  //   (a) Multi-human group (target_human_count > 1) → real lobby.
  //       Show "Finding a chat partner…" until the lobby closes
  //       (other humans arrived or deadline reached) or aborts (410).
  //       No cosmetic simulated wait on top — the lobby IS the wait.
  //   (b) Single-human (one_on_one or group with target=1) with a
  //       simulate_pairing_seconds > 0 → cosmetic timed wait. Backend
  //       closes the lobby automatically since target=1 is reached on
  //       this caller's join.
  //   (c) Single-human and no simulate_pairing_seconds → just prefetch.
  //
  // Mirrors the editor visibility rule documented in
  // docs/low-level-design.md "Mode UX".
  const isMultiHumanGroup =
    setting?.mode === "group" && (setting?.target_human_count ?? 0) > 1;
  const rawPairingSeconds = setting?.simulate_pairing_seconds || 0;

  if (isMultiHumanGroup) {
    renderPairingScreen(options.element);
    await state.prefetchEvents();

    // If the lobby was already closed by the time prefetch ran (e.g. the
    // last human just arrived), skip the wait. Otherwise watch the lobby
    // until it closes or aborts. We use the dedicated lobby-poll loop
    // (not startPolling) so events that land at lobby-close don't get
    // dispatched into the still-pairing-screen DOM. After it closes, we
    // re-prefetch from the top so the conversation row's join/system
    // events show up in chat history when we finally render the UI.
    if (state.isInLobby()) {
      try {
        await state.pollLobbyUntilClosed();
      } catch (err) {
        if (err instanceof Error && err.message === "lobby_aborted") {
          // onLobbyAborted (registered above) has already swapped the DOM
          // to the abort screen. Bail before rendering chat UI.
          return;
        }
        throw err;
      }
      // Lobby closed: re-fetch the now-visible conversation history so
      // the renderer replays joins + system events from the start.
      await state.prefetchEvents();
    }
  } else if (rawPairingSeconds > 0) {
    await Promise.all([
      showPairingScreen(options.element, rawPairingSeconds),
      state.prefetchEvents(),
    ]);
  } else {
    await state.prefetchEvents();
  }

  // 5. Render chatroom UI
  renderChatroom(options.element, (text: string) => {
    state!.send(text);
  });

  // 6. Replay prefetched events into the UI
  state.replayPrefetchedEvents();

  // 7. Start polling
  state.startPolling();

  // 8. Start timer if configured
  const minMin = setting?.timer_min_minutes;
  const maxMin = setting?.timer_max_minutes;
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
