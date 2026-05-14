declare const $: JQueryStatic;
declare const jQuery: JQueryStatic;
const _$ = (typeof jQuery !== "undefined" ? jQuery : $) as JQueryStatic;

export function showPairingScreen(
  element: string | HTMLElement,
  seconds: number
): Promise<void> {
  const $el = _$(element as any) as JQuery;
  $el.html(`
    <div class="stim-chatroom">
      <div class="stim-pairing">
        <span>Finding a chat partner<span class="stim-pairing-dots"></span></span>
      </div>
    </div>
  `);
  return new Promise((resolve) => setTimeout(resolve, seconds * 1000));
}
