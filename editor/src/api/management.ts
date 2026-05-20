/**
 * Thin wrapper around `fetch` that targets the management API and attaches
 * the bearer token from build-time env. See docs/low-level-design.md
 * "Editor → Management API".
 */
import { MANAGEMENT_API_URL, MANAGEMENT_API_TOKEN } from '../config'

export async function mgmtFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${MANAGEMENT_API_TOKEN}`)
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  return fetch(`${MANAGEMENT_API_URL}${path}`, { ...init, headers })
}
