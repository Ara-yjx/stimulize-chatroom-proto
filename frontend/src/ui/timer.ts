export function formatTimerText(
  elapsedMinutes: number,
  minMinutes?: number,
  maxMinutes?: number
): string {
  let text = `Now: ${elapsedMinutes} minute${elapsedMinutes !== 1 ? "s" : ""}.`;
  if (minMinutes && maxMinutes) {
    text = `Please stay ${minMinutes} to ${maxMinutes} minutes in the chatroom. ${text}`;
  } else if (minMinutes) {
    text = `Please stay at least ${minMinutes} minutes. ${text}`;
  } else if (maxMinutes) {
    text = `Please stay up to ${maxMinutes} minutes. ${text}`;
  }
  return text;
}
