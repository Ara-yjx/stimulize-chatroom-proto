import { _$ } from "../lib/jquery";

let _$banner: JQuery | null = null;

export function showReconnectBanner(container: string | HTMLElement): void {
  if (_$banner) return;
  const $el = _$(container as any) as JQuery;
  _$banner = _$(`<div class="stim-reconnect-banner">Reconnecting…</div>`);
  $el.find(".stim-chatroom").prepend(_$banner);
}

export function hideReconnectBanner(): void {
  if (_$banner) {
    _$banner.remove();
    _$banner = null;
  }
}
