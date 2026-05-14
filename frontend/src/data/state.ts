import type {
  Avatar,
  ChatMessage,
  ChatroomSetting,
  ConversationEvent,
  InitOptions,
  SessionInfo,
} from "./types";
import { exchangeToken, sendMessage, pollMessages } from "./api";

// --- Callback types ---
export type OnMessageCallback = (msg: ChatMessage, isSelf: boolean) => void;
export type OnSystemEventCallback = (content: string) => void;
export type OnErrorCallback = (content: string) => void;
export type OnSessionReadyCallback = (info: SessionInfo) => void;
export type OnTimerTickCallback = (elapsedMinutes: number) => void;

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

  private _apiBaseUrl = "";
  private _pollingTimer: ReturnType<typeof setInterval> | null = null;
  private _timerInterval: ReturnType<typeof setInterval> | null = null;
  private _chatStartTime = 0;
  private _prefetchedEvents: ConversationEvent[] = [];

  // Callback registries
  private _onMessage: OnMessageCallback[] = [];
  private _onSystemEvent: OnSystemEventCallback[] = [];
  private _onError: OnErrorCallback[] = [];
  private _onSessionReady: OnSessionReadyCallback[] = [];
  private _onTimerTick: OnTimerTickCallback[] = [];

  // --- Register callbacks ---
  onMessage(cb: OnMessageCallback): void { this._onMessage.push(cb); }
  onSystemEvent(cb: OnSystemEventCallback): void { this._onSystemEvent.push(cb); }
  onError(cb: OnErrorCallback): void { this._onError.push(cb); }
  onSessionReady(cb: OnSessionReadyCallback): void { this._onSessionReady.push(cb); }
  onTimerTick(cb: OnTimerTickCallback): void { this._onTimerTick.push(cb); }

  // --- Init: exchange token, populate state ---
  async init(options: InitOptions): Promise<void> {
    this._apiBaseUrl = options.apiBaseUrl.replace(/\/+$/, "");
    this.chatHistory = [];
    this.lastTimestamp = 0;

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
    // Optimistic UI — push user message immediately
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
      const resp = await sendMessage(this._apiBaseUrl, this.token, text);

      if (resp.replies) {
        for (const reply of resp.replies) {
          const aiMsg: ChatMessage = {
            sender: reply.nickname,
            content: reply.content,
            role: "ai",
            timestamp: Date.now(),
            avatar: reply.avatar,
          };
          this.chatHistory.push(aiMsg);
          this._onMessage.forEach((cb) => cb(aiMsg, false));
        }
      }

      if (resp.error) {
        const errMsg = "Chatroom server error";
        this.chatHistory.push({
          sender: "System",
          content: errMsg,
          role: "system",
          timestamp: Date.now(),
        });
        this._onError.forEach((cb) => cb(errMsg));
      }
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

  private async _poll(): Promise<void> {
    try {
      const resp = await pollMessages(this._apiBaseUrl, this.token, this.lastTimestamp);
      if (!resp.events) return;

      for (const evt of resp.events) {
        this._processEvent(evt);
      }
    } catch (err: any) {
      if (err?.status === 401) {
        this.stopPolling();
      }
    }
  }

  private _processEvent(evt: ConversationEvent): void {
    // Skip own messages (already shown via optimistic UI)
    if (evt.type === "message" && evt.session_id === this.sessionId) return;

    if (evt.type === "system") {
      let content = evt.content;
      if (content === `${this.nickname} joined the chatroom`) {
        content = `${this.nickname} (you) joined the chatroom`;
      }
      this.chatHistory.push({
        sender: evt.sender,
        content,
        role: "system",
        timestamp: evt.timestamp,
      });
      this._onSystemEvent.forEach((cb) => cb(content));
    } else if (evt.type === "error") {
      const errMsg = "Chatroom server error";
      this.chatHistory.push({
        sender: "System",
        content: errMsg,
        role: "system",
        timestamp: evt.timestamp,
      });
      this._onError.forEach((cb) => cb(errMsg));
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

    if (evt.timestamp > this.lastTimestamp) {
      this.lastTimestamp = evt.timestamp;
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
