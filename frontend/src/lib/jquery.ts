import jquery from "jquery";

declare const jQuery: JQueryStatic | undefined;

export const _$ = (typeof jQuery !== "undefined" ? jQuery : jquery) as JQueryStatic;
