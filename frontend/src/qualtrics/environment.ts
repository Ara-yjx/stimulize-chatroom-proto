export function getQualtricsPreviewFrameId(): string | null {
  try {
    const id = window.frameElement?.id;
    return typeof id === "string" && id ? id : null;
  } catch {
    return null;
  }
}

export function isQualtricsMobilePreview(): boolean {
  return getQualtricsPreviewFrameId() === "mobile-preview-view";
}
