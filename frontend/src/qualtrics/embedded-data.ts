/**
 * Write chat history to Qualtrics Embedded Data if available.
 * Called on every message event.
 */
export function writeToED(historyText: string): void {
  try {
    const Q = (window as any).Qualtrics;
    if (Q && Q.SurveyEngine && typeof Q.SurveyEngine.setEmbeddedData === "function") {
      Q.SurveyEngine.setEmbeddedData("chatroom_history", historyText);
    }
  } catch {
    // Silently ignore — not in Qualtrics environment
  }
}
