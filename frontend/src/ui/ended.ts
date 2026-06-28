import { _$ } from "../lib/jquery";

export function showConversationEnded(container: string | HTMLElement): void {
  const $el = _$(container as any) as JQuery;
  $el.find(".stim-input input").prop("disabled", true).attr("placeholder", "Conversation ended");
  $el.find(".stim-input button").prop("disabled", true);
}
