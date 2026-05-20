declare const $: JQueryStatic;
declare const jQuery: JQueryStatic;
const _$ = (typeof jQuery !== "undefined" ? jQuery : $) as JQueryStatic;

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
  onSend: (text: string) => void
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
}

export function appendBubble(
  sender: string,
  content: string,
  isSelf: boolean,
  emojiText?: string
): void {
  if (!_$messages) return;
  const cls = isSelf ? "stim-msg-self" : "stim-msg-other";
  const avatarPrefix = emojiText ? `${escapeHtml(emojiText)} ` : "";
  _$messages.append(`
    <div class="stim-msg ${cls}">
      <span class="stim-nickname">${avatarPrefix}${escapeHtml(sender)}</span>
      <span class="stim-bubble">${escapeHtml(content)}</span>
    </div>
  `);
  scrollToBottom();
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
