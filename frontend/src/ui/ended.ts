declare const $: JQueryStatic;
declare const jQuery: JQueryStatic;
const _$ = (typeof jQuery !== "undefined" ? jQuery : $) as JQueryStatic;

export function showConversationEnded(container: string | HTMLElement): void {
  const $el = _$(container as any) as JQuery;
  $el.find(".stim-input input").prop("disabled", true).attr("placeholder", "Conversation ended");
  $el.find(".stim-input button").prop("disabled", true);
}
