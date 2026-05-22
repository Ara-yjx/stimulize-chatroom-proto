/**
 * Build-time configuration for the editor. Reads VITE_-prefixed env vars
 * (Vite only exposes those to client code) with sensible local-dev defaults.
 *
 * See docs/low-level-design.md "Management API hostname configuration" and
 * "Local Dev Setup".
 */

function normalizeApiBaseUrl(value: string | undefined, fallback: string): string {
  return (value || fallback).replace(/\/+$/, '')
}

function hostedWidgetUrl(): string {
  const basePath = `${import.meta.env.BASE_URL}chatroom.min.js`
  if (typeof window === 'undefined') return basePath
  return new URL(basePath, window.location.origin).toString()
}

/** Management API base URL (mock Flask in beta; real Stimulize backend in prod). */
export const MANAGEMENT_API_URL: string =
  normalizeApiBaseUrl(import.meta.env.VITE_MOCK_MGMT_URL as string | undefined, 'http://localhost:5000')

/** Bearer token for the management API. */
export const MANAGEMENT_API_TOKEN: string =
  (import.meta.env.VITE_MOCK_MGMT_TOKEN as string | undefined) ?? ''

/** Optional dev-only login credentials to mint a fresh management token on demand. */
export const MANAGEMENT_API_USERNAME: string =
  (import.meta.env.VITE_MOCK_MGMT_USERNAME as string | undefined) ?? ''

export const MANAGEMENT_API_PASSWORD: string =
  (import.meta.env.VITE_MOCK_MGMT_PASSWORD as string | undefined) ?? ''

/** Chatroom (widget runtime) API base URL — used by the embed script + preview. */
export const CHATROOM_API_URL: string =
  normalizeApiBaseUrl(import.meta.env.VITE_CHATROOM_API_URL as string | undefined, 'http://localhost:5001')

/** Optional separate widget bundle URL for dev/beta preview flows. */
export const CHATROOM_WIDGET_URL: string =
  (import.meta.env.VITE_CHATROOM_WIDGET_URL as string | undefined)
    ?? (import.meta.env.DEV ? `${CHATROOM_API_URL}/chatroom.min.js` : hostedWidgetUrl())

console.log({
  MANAGEMENT_API_URL,
  CHATROOM_API_URL,
  CHATROOM_WIDGET_URL,
})
