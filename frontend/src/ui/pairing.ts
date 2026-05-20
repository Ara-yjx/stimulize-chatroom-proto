declare const $: JQueryStatic;
declare const jQuery: JQueryStatic;
const _$ = (typeof jQuery !== "undefined" ? jQuery : $) as JQueryStatic;

/**
 * Render the pairing screen HTML into *element*. Returns nothing — caller
 * decides when to replace the HTML (typically by rendering the chatroom UI
 * once the lobby closes, or by ``showLobbyAborted`` on 410).
 */
export function renderPairingScreen(element: string | HTMLElement): void {
  const $el = _$(element as any) as JQuery;
  $el.html(`
    <div class="stim-chatroom">
      <div class="stim-pairing">
        <span>Finding a chat partner<span class="stim-pairing-dots"></span></span>
      </div>
    </div>
  `);
}

export function showPairingScreen(
  element: string | HTMLElement,
  seconds: number
): Promise<void> {
  renderPairingScreen(element);
  return new Promise((resolve) => setTimeout(resolve, seconds * 1000));
}

export function showLobbyAborted(
  element: string | HTMLElement,
  onReconnect: () => void
): void {
  const $el = _$(element as any) as JQuery;
  $el.html(`
    <div class="stim-chatroom">
      <div class="stim-pairing">
        <span>No one else joined this chatroom.</span>
        <button class="stim-reconnect-btn">Reconnect</button>
      </div>
    </div>
  `);
  $el.find(".stim-reconnect-btn").on("click", onReconnect);
}
