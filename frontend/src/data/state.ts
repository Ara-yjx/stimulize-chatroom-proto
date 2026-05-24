import type {
  Avatar,
  ChatMessage,
  ChatroomSetting,
  ConversationEvent,
  InitOptions,
  LobbyState,
  SessionInfo,
} from "./types";
import { exchangeToken, sendMessage, pollMessages } from "./api";
const DEFAULT_API_BASE_URL = "https://pmvb4orly5.execute-api.us-east-2.amazonaws.com/prod";

// --- Callback types ---
export type OnMessageCallback = (msg: ChatMessage, isSelf: boolean) => void;
export type OnSystemEventCallback = (content: string) => void;
export type OnErrorCallback = (content: string) => void;
export type OnSessionReadyCallback = (info: SessionInfo) => void;
export type OnTimerTickCallback = (elapsedMinutes: number) => void;
export type OnLobbyUpdateCallback = (lobby: LobbyState) => void;
export type OnConversationEndedCallback = () => void;
export type OnLobbyAbortedCallback = () => void;
export type OnLobbyClosedCallback = () => void;

export class ChatroomState {
  // Session state
  token = "";
  sessionId = "";
  conversationId = "";
  nickname = "";
  avatar: Avatar = { emojiText: "" };
  chatroomSetting: ChatroomSetting | null = null;
  chatHistory: ChatMessage[] = [];
  lastTimestamp = 0;
  conversationStatus: "active" | "ended" | "lobby" = "lobby";

  private _apiBaseUrl = "";
  private _pollingTimer: ReturnType<typeof setInterval> | null = null;
  private _timerInterval: ReturnType<typeof setInterval> | null = null;
  private _chatStartTime = 0;
  private _prefetchedEvents: ConversationEvent[] = [];
  private _pendingEvents: Array<{ evt: ConversationEvent; timer: ReturnType<typeof setTimeout> }> = [];
  private _initOptions: InitOptions | null = null;
  private _pollFailingSince: number | null = null;
  /** Tracks whether we've seen a lobby block in any poll response so far. */
  private _sawLobby = false;
  private _lobbyPollTimer: ReturnType<typeof setInterval> | null = null;

  // Callback registries
  private _onMessage: OnMessageCallback[] = [];
  private _onSystemEvent: OnSystemEventCallback[] = [];
  private _onError: OnErrorCallback[] = [];
  private _onSessionReady: OnSessionReadyCallback[] = [];
  private _onTimerTick: OnTimerTickCallback[] = [];
  private _onLobbyUpdate: OnLobbyUpdateCallback[] = [];
  private _onConversationEnded: OnConversationEndedCallback[] = [];
  private _onLobbyAborted: OnLobbyAbortedCallback[] = [];
  private _onLobbyClosed: OnLobbyClosedCallback[] = [];
  private _onReconnecting: Array<(reconnecting: boolean) => void> = [];

  // --- Register callbacks ---
  onMessage(cb: OnMessageCallback): void { this._onMessage.push(cb); }
  onSystemEvent(cb: OnSystemEventCallback): void { this._onSystemEvent.push(cb); }
  onError(cb: OnErrorCallback): void { this._onError.push(cb); }
  onSessionReady(cb: OnSessionReadyCallback): void { this._onSessionReady.push(cb); }
  onTimerTick(cb: OnTimerTickCallback): void { this._onTimerTick.push(cb); }
  onLobbyUpdate(cb: OnLobbyUpdateCallback): void { this._onLobbyUpdate.push(cb); }
  onConversationEnded(cb: OnConversationEndedCallback): void { this._onConversationEnded.push(cb); }
  onLobbyAborted(cb: OnLobbyAbortedCallback): void { this._onLobbyAborted.push(cb); }
  /** Fires once when the lobby phase ends (server returned the conversation row). */
  onLobbyClosed(cb: OnLobbyClosedCallback): void { this._onLobbyClosed.push(cb); }
  onReconnecting(cb: (reconnecting: boolean) => void): void { this._onReconnecting.push(cb); }

  getInitOptions(): InitOptions | null { return this._initOptions; }

  /**
   * True iff prefetch has seen a ``lobby`` block in its response and the
   * lobby has not yet closed/aborted. Used by the widget to decide whether
   * to keep the pairing screen up or jump straight to the chat UI.
   */
  isInLobby(): boolean {
    return this._sawLobby;
  }

  // --- Init: exchange token, populate state ---
  async init(options: InitOptions): Promise<void> {
    this._initOptions = options;
    this._apiBaseUrl = (options.apiBaseUrl || DEFAULT_API_BASE_URL).replace(/\/+$/, "");
    this.chatHistory = [];
    this.lastTimestamp = 0;
    this.conversationStatus = "lobby";
    this._pollFailingSince = null;
    this._sawLobby = false;

    const resp = await exchangeToken(this._apiBaseUrl, options.chatroomId);
    this.token = resp.token;
    this.sessionId = resp.session_id;
    this.conversationId = resp.conversation_id;
    this.nickname = resp.nickname;
    this.avatar = resp.avatar;
    this.chatroomSetting = resp.chatroom_setting;

    const info: SessionInfo = {
      token: this.token,
      sessionId: this.sessionId,
      conversationId: this.conversationId,
      nickname: this.nickname,
      avatar: this.avatar,
      chatroomSetting: this.chatroomSetting,
    };
    this._onSessionReady.forEach((cb) => cb(info));
  }

  // --- Prefetch: load initial events during pairing screen ---
  async prefetchEvents(): Promise<void> {
    try {
      const resp = await pollMessages(this._apiBaseUrl, this.token, 0);
      if (resp.events) {
        this._prefetchedEvents = resp.events;
      }
      if (resp.conversation_status) {
        this.conversationStatus = resp.conversation_status;
      }
      // Seed lobby-phase tracker so the regular poll loop knows we
      // started in the lobby and can fire onLobbyClosed when it ends.
      if (resp.lobby) {
        this._sawLobby = true;
        this._onLobbyUpdate.forEach((cb) => cb(resp.lobby!));
      }
    } catch {
      // Silently ignore — events will be picked up by regular polling
    }
  }

  // --- Replay prefetched events into callbacks (called after UI is rendered) ---
  replayPrefetchedEvents(): void {
    for (const evt of this._prefetchedEvents) {
      this._processEvent(evt);
    }
    this._prefetchedEvents = [];
  }

  // --- Send message ---
  async send(text: string): Promise<void> {
    if (this.conversationStatus === "ended") return;

    const userMsg: ChatMessage = {
      sender: this.nickname,
      content: text,
      role: "user",
      timestamp: Date.now(),
      session_id: this.sessionId,
      avatar: this.avatar,
    };
    this.chatHistory.push(userMsg);
    this._onMessage.forEach((cb) => cb(userMsg, true));

    try {
      await sendMessage(this._apiBaseUrl, this.token, text);
    } catch (err: any) {
      if (err?.status === 401) {
        this._onError.forEach((cb) => cb("Session expired. Please refresh."));
        return;
      }
      this._onError.forEach((cb) => cb("Failed to send message."));
    }
  }

  // --- Polling ---
  startPolling(): void {
    if (this._pollingTimer) return;
    this._pollingTimer = setInterval(() => this._poll(), 3000);
  }

  stopPolling(): void {
    if (this._pollingTimer) {
      clearInterval(this._pollingTimer);
      this._pollingTimer = null;
    }
  }

  /**
   * Poll the lobby state every 3s while waiting for it to close. Unlike
   * ``startPolling``, this does NOT fan events out to the chat-UI
   * callbacks — it only watches for the lobby block to disappear (or for
   * a 410 abort). Returns a Promise that resolves on lobby close and
   * rejects with an error tagged ``"lobby_aborted"`` on 410. Always stops
   * the lobby-poll timer before resolving/rejecting.
   *
   * Used by the multi-human group flow: keep the pairing screen up,
   * watch for the lobby to end, then ``prefetchEvents`` again from the
   * top so the rendered conversation includes the joins/system events
   * the server wrote at lobby-close.
   */
  pollLobbyUntilClosed(): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      const stop = () => {
        if (this._lobbyPollTimer) {
          clearInterval(this._lobbyPollTimer);
          this._lobbyPollTimer = null;
        }
      };
      const tick = async () => {
        try {
          // ``after`` is irrelevant — we ignore the events array here.
          const resp = await pollMessages(this._apiBaseUrl, this.token, 0);
          if (resp.lobby) {
            this._sawLobby = true;
            this._onLobbyUpdate.forEach((cb) => cb(resp.lobby!));
            return;
          }
          // No lobby block → conversation row exists, lobby has closed.
          stop();
          this._sawLobby = false;
          this._onLobbyClosed.forEach((cb) => cb());
          resolve();
        } catch (err: any) {
          if (err?.status === 410) {
            stop();
            this._onLobbyAborted.forEach((cb) => cb());
            reject(new Error("lobby_aborted"));
          }
          // Other errors: keep trying. The reconnect banner is for the
          // chat phase; lobby waits don't surface intermittent failures.
        }
      };
      this._lobbyPollTimer = setInterval(tick, 3000);
    });
  }

  private async _poll(): Promise<void> {
    try {
      const resp = await pollMessages(this._apiBaseUrl, this.token, this.lastTimestamp);

      // Poll success — clear reconnect state
      if (this._pollFailingSince !== null) {
        this._pollFailingSince = null;
        this._onReconnecting.forEach((cb) => cb(false));
      }

      // ``startPolling`` runs only after the lobby has closed (chat-phase
      // poll). Lobby-phase tracking lives in ``pollLobbyUntilClosed``; we
      // shouldn't see a ``lobby`` block here, but if we do (server bug,
      // race during reconnect), forward it to listeners and skip events.
      if (resp.lobby) {
        this._onLobbyUpdate.forEach((cb) => cb(resp.lobby!));
        return;
      }

      // Handle conversation status
      if (resp.conversation_status === "ended" && this.conversationStatus !== "ended") {
        this.conversationStatus = "ended";
        // Process any final events first
        if (resp.events) {
          for (const evt of resp.events) {
            this._processEvent(evt);
          }
        }
        this._onConversationEnded.forEach((cb) => cb());
        this.stopPolling();
        this.stopTimer();
        return;
      }

      if (resp.conversation_status === "active" && this.conversationStatus === "lobby") {
        this.conversationStatus = "active";
      }

      if (!resp.events) return;
      for (const evt of resp.events) {
        this._processEvent(evt);
      }
    } catch (err: any) {
      if (err?.status === 401) {
        this.stopPolling();
        this._onError.forEach((cb) => cb("Session expired. Please refresh."));
        return;
      }
      if (err?.status === 410) {
        this.stopPolling();
        this._onLobbyAborted.forEach((cb) => cb());
        return;
      }

      // Track continuous failures for reconnect banner
      const now = Date.now();
      if (this._pollFailingSince === null) {
        this._pollFailingSince = now;
      } else if (now - this._pollFailingSince >= 30000) {
        this._onReconnecting.forEach((cb) => cb(true));
      }
    }
  }

  private _processEvent(evt: ConversationEvent): void {
    // Skip own messages (already shown via optimistic UI)
    if (evt.type === "message" && evt.session_id === this.sessionId) return;

    // Advance the polling cursor by visible_at (matches the server's
    // `?after` filter, which compares visible_at). Using `timestamp` here
    // causes the same event to be re-delivered every poll until visible_at
    // passes (typing delay window), producing duplicates in chatHistory.
    const visibleAt = evt.visible_at ?? evt.timestamp;
    if (visibleAt > this.lastTimestamp) {
      this.lastTimestamp = visibleAt;
    }

    // visible_at scheduling: defer rendering if event isn't visible yet
    const now = Date.now();
    if (visibleAt > now) {
      const delay = visibleAt - now;
      const timer = setTimeout(() => {
        this._pendingEvents = this._pendingEvents.filter((p) => p.timer !== timer);
        this._renderEvent(evt);
      }, delay);
      this._pendingEvents.push({ evt, timer });
      return;
    }

    this._renderEvent(evt);
  }

  private _renderEvent(evt: ConversationEvent): void {
    if (evt.type === "system") {
      let content = evt.content;
      if (evt.session_id === this.sessionId) {
        content = content.replace(this.nickname, `${this.nickname} (you)`);
      }
      this.chatHistory.push({
        sender: evt.sender,
        content,
        role: "system",
        timestamp: evt.timestamp,
      });
      this._onSystemEvent.forEach((cb) => cb(content));
    } else if (evt.type === "error") {
      this.chatHistory.push({
        sender: "System",
        content: evt.content,
        role: "system",
        timestamp: evt.timestamp,
      });
      this._onError.forEach((cb) => cb(evt.content));
    } else {
      const msg: ChatMessage = {
        sender: evt.sender,
        content: evt.content,
        role: evt.role === "ai" ? "ai" : "user",
        timestamp: evt.timestamp,
        session_id: evt.session_id,
        avatar: evt.avatar,
      };
      this.chatHistory.push(msg);
      this._onMessage.forEach((cb) => cb(msg, false));
    }
  }

  // --- Timer ---
  startTimer(minMinutes?: number, maxMinutes?: number): void {
    if (!minMinutes && !maxMinutes) return;
    this._chatStartTime = Date.now();

    this._timerInterval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - this._chatStartTime) / 60000);
      this._onTimerTick.forEach((cb) => cb(elapsed));
    }, 1000);
  }

  stopTimer(): void {
    if (this._timerInterval) {
      clearInterval(this._timerInterval);
      this._timerInterval = null;
    }
  }

  // --- Cleanup ---
  destroy(): void {
    this.stopPolling();
    if (this._lobbyPollTimer) {
      clearInterval(this._lobbyPollTimer);
      this._lobbyPollTimer = null;
    }
    this.stopTimer();
    for (const p of this._pendingEvents) {
      clearTimeout(p.timer);
    }
    this._pendingEvents = [];
  }

  // --- History accessors ---
  getHistory(): ChatMessage[] {
    return [...this.chatHistory];
  }

  getHistoryText(): string {
    return this.chatHistory
      .map((m) => {
        if (m.role === "system") return `[SYS] ${m.content}`;
        if (m.role === "ai") return `[${m.sender}] [AI] ${m.content}`;
        return `[${m.sender}] ${m.content}`;
      })
      .join("\n");
  }
}
