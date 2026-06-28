import type { InitOptions } from "../data/types";
import { getQualtricsPreviewFrameId } from "./environment";

type QualtricsQuestionLike = {
  hideNextButton?: () => void;
  showNextButton?: () => void;
};

function isKnownNonRealQualtricsPage(): boolean {
  const host = window.location.hostname.toLowerCase();
  return (
    host === "localhost" ||
    host === "127.0.0.1" ||
    host.endsWith(".github.io") ||
    getQualtricsPreviewFrameId() !== null
  );
}

function getQuestionLike(options: InitOptions): QualtricsQuestionLike | null {
  if (
    options.qualtricsQuestion &&
    typeof options.qualtricsQuestion.hideNextButton === "function" &&
    typeof options.qualtricsQuestion.showNextButton === "function"
  ) {
    return options.qualtricsQuestion;
  }

  try {
    const surveyEngine = typeof Qualtrics !== "undefined" ? Qualtrics?.SurveyEngine : null;
    if (
      surveyEngine &&
      typeof surveyEngine.hideNextButton === "function" &&
      typeof surveyEngine.showNextButton === "function"
    ) {
      return surveyEngine;
    }
  } catch {
    // Ignore missing global binding.
  }

  return null;
}

/**
 * Hide Qualtrics Next until the minimum timer is reached.
 *
 * `timer_max_minutes` ends the chat; it must not control Next availability.
 * Returning an unlock callback keeps the timer ownership in the widget while
 * keeping the Qualtrics-specific API surface isolated here.
 */
export function lockQualtricsNextUntilTimerMin(
  options: InitOptions,
  minMinutes: number | null | undefined
): (elapsedMinutes: number) => void {
  if (!minMinutes || minMinutes <= 0 || isKnownNonRealQualtricsPage()) {
    return () => {};
  }

  const question = getQuestionLike(options);
  if (!question) return () => {};

  let unlocked = false;
  try {
    question.hideNextButton?.();
  } catch {
    return () => {};
  }

  return (elapsedMinutes: number) => {
    if (unlocked || elapsedMinutes < minMinutes) return;
    unlocked = true;
    try {
      question.showNextButton?.();
    } catch {
      // Ignore; this should never break the widget.
    }
  };
}
