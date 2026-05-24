import type { ChatMessage } from "../data/types";

const QUALTRICS_CHATROOM_HISTORY = "QUALTRICS_CHATROOM_HISTORY";
const QUALTRICS_CHATROOM_HISTORY_JSON = "QUALTRICS_CHATROOM_HISTORY_JSON";

type SurveyEngineLike = {
  setEmbeddedData?: (key: string, value: string) => void;
};

function getSurveyEngine(): SurveyEngineLike | null {
  try {
    const direct = typeof Qualtrics !== "undefined" ? Qualtrics?.SurveyEngine : null;
    if (direct && typeof direct.setEmbeddedData === "function") {
      return direct;
    }
  } catch {
    // Ignore missing global binding.
  }

  return null;
}

function isPreviewEnvironment(): boolean {
  const host = window.location.hostname.toLowerCase();
  if (
    host === "localhost" ||
    host === "127.0.0.1" ||
    host.endsWith(".github.io")
  ) {
    return true;
  }

  const href = window.location.href;
  return href.includes("preview-check.html") || href.includes("local-full-run-check.html");
}

/**
 * Write chat history to Qualtrics Embedded Data when the widget is running
 * inside a real Qualtrics page. Preview environments should not emit writes.
 */
export function writeToED(history: ChatMessage[], historyText: string): void {
  try {
    const surveyEngine = getSurveyEngine();
    if (!surveyEngine || typeof surveyEngine.setEmbeddedData !== "function") {
      return;
    }

    if (isPreviewEnvironment()) {
      return;
    }

    surveyEngine.setEmbeddedData(QUALTRICS_CHATROOM_HISTORY, historyText);
    surveyEngine.setEmbeddedData(
      QUALTRICS_CHATROOM_HISTORY_JSON,
      JSON.stringify(history)
    );
  } catch {
    // Silently ignore — embedded data writes must not break the widget.
  }
}
