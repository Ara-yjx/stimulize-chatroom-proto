/**
 * Build-time configuration for the editor. Reads VITE_-prefixed env vars
 * (Vite only exposes those to client code) with sensible local-dev defaults.
 *
 * See docs/low-level-design.md "Management API hostname configuration" and
 * "Local Dev Setup".
 */

/** Management API base URL (mock Flask in beta; real Stimulize backend in prod). */
export const MANAGEMENT_API_URL: string =
  (import.meta.env.VITE_MOCK_MGMT_URL as string | undefined) || 'http://localhost:5000'

/** Bearer token for the management API. */
export const MANAGEMENT_API_TOKEN: string =
  (import.meta.env.VITE_MOCK_MGMT_TOKEN as string | undefined) || 'dev-mgmt-token'

/** Chatroom (widget runtime) API base URL — used by the embed script + preview. */
export const CHATROOM_API_URL: string =
  (import.meta.env.VITE_CHATROOM_API_URL as string | undefined) || 'http://localhost:5001'

console.log({
  MANAGEMENT_API_URL,
  CHATROOM_API_URL,
})
