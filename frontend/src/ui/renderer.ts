import { _$ } from "../lib/jquery";
import type { ChatMessage } from "../data/types";

export type RenderedHistoryMessage = ChatMessage & { isSelf: boolean };

let _$messages: JQuery | null = null;
let _$input: JQuery | null = null;
let _$btn: JQuery | null = null;
let _$timer: JQuery | null = null;

function escapeHtml(text: string): string {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

export function renderChatroom(
  element: string | HTMLElement,
  onSend: (text: string) => void,
  onLoadOlder?: () => Promise<RenderedHistoryMessage[]>
): void {
  const $el = _$(element as any) as JQuery;
  $el.html(`
    <div class="stim-chatroom">
      <div class="stim-messages"></div>
      <div class="stim-input">
        <input type="text" placeholder="Type a message..." />
        <button>Send</button>
      </div>
      <div class="stim-timer" style="display:none;"></div>
    </div>
  `);

  _$messages = $el.find(".stim-messages");
  _$input = $el.find(".stim-input input");
  _$btn = $el.find(".stim-input button");
  _$timer = $el.find(".stim-timer");

  const doSend = () => {
    const text = (_$input!.val() as string || "").trim();
    if (!text) return;
    _$input!.val("");
    onSend(text);
  };

  _$btn!.on("click", doSend);
  _$input!.on("keydown", (e: JQuery.KeyDownEvent) => {
    if (e.key === "Enter") doSend();
  });

  let loadingOlder = false;
  _$messages.on("scroll", async () => {
    const el = _$messages?.[0];
    if (!el || el.scrollTop > 32 || loadingOlder || !onLoadOlder) return;
    loadingOlder = true;
    const oldHeight = el.scrollHeight;
    try {
      const messages = await onLoadOlder();
      prependHistory(messages);
      el.scrollTop = el.scrollHeight - oldHeight;
    } finally {
      loadingOlder = false;
    }
  });
}

function bubbleHtml(
  sender: string,
  content: string,
  isSelf: boolean,
  emojiText?: string
): string {
  const cls = isSelf ? "stim-msg-self" : "stim-msg-other";
  const avatarPrefix = emojiText ? `${escapeHtml(emojiText)} ` : "";
  return `
    <div class="stim-msg ${cls}">
      <span class="stim-nickname">${avatarPrefix}${escapeHtml(sender)}</span>
      <span class="stim-bubble">${escapeHtml(content)}</span>
    </div>
  `;
}

export function appendBubble(
  sender: string,
  content: string,
  isSelf: boolean,
  emojiText?: string
): void {
  if (!_$messages) return;
  _$messages.append(bubbleHtml(sender, content, isSelf, emojiText));
  scrollToBottom();
}

export function prependHistory(messages: RenderedHistoryMessage[]): void {
  if (!_$messages || !messages.length) return;
  const html = messages.map((message) => {
    if (message.role === "system") {
      return `
        <div class="stim-msg stim-msg-system">
          <span class="stim-bubble">${escapeHtml(message.content)}</span>
        </div>
      `;
    }
    return bubbleHtml(
      message.sender,
      message.content,
      message.isSelf,
      message.avatar?.emojiText
    );
  }).join("");
  _$messages.prepend(html);
}

export function appendSystemBubble(content: string): void {
  if (!_$messages) return;
  _$messages.append(`
    <div class="stim-msg stim-msg-system">
      <span class="stim-bubble">${escapeHtml(content)}</span>
    </div>
  `);
  scrollToBottom();
}

export function appendErrorBubble(content: string): void {
  if (!_$messages) return;
  _$messages.append(`
    <div class="stim-msg stim-msg-system stim-msg-error">
      <span class="stim-bubble">${escapeHtml(content)}</span>
    </div>
  `);
  scrollToBottom();
}

export function updateTimerBar(text: string): void {
  if (!_$timer) return;
  _$timer.show().text(text);
}

export function scrollToBottom(): void {
  if (!_$messages) return;
  const el = _$messages[0];
  el.scrollTop = el.scrollHeight;
}
