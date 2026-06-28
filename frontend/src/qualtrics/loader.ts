import { _$ } from "../lib/jquery";

/**
 * Load a script via jQuery.getScript (useful in Qualtrics).
 */
export function loadScript(url: string): Promise<void> {
  return new Promise((resolve, reject) => {
    _$.getScript(url)
      .done(() => resolve())
      .fail((_jqxhr: any, _settings: any, exception: any) => reject(exception));
  });
}
