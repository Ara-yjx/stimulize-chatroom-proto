/**
 * Thin wrapper around `fetch` that targets the management API and attaches
 * the bearer token from build-time env. See docs/low-level-design.md
 * "Editor → Management API".
 */
import {
  MANAGEMENT_API_PASSWORD,
  MANAGEMENT_API_TOKEN,
  MANAGEMENT_API_URL,
  MANAGEMENT_API_USERNAME,
} from '../config'

let cachedToken = MANAGEMENT_API_TOKEN
let loginPromise: Promise<string> | null = null

function unwrapPayload<T>(payload: unknown): T {
  if (!payload || typeof payload !== 'object') return payload as T

  const maybeData = (payload as { data?: unknown }).data
  if (!maybeData || typeof maybeData !== 'object' || Array.isArray(maybeData)) {
    return payload as T
  }

  if ('chatrooms' in maybeData) return (maybeData as { chatrooms: T }).chatrooms
  if ('chatroom' in maybeData) return (maybeData as { chatroom: T }).chatroom

  return maybeData as T
}

async function loginForToken(): Promise<string> {
  if (!MANAGEMENT_API_USERNAME || !MANAGEMENT_API_PASSWORD) {
    throw new Error('Management API credentials are not configured')
  }
  if (loginPromise) return loginPromise

  loginPromise = (async () => {
    const resp = await fetch(`${MANAGEMENT_API_URL}/api/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: MANAGEMENT_API_USERNAME,
        password: MANAGEMENT_API_PASSWORD,
      }),
    })

    const payload = await resp.json().catch(() => ({}))
    if (!resp.ok) {
      const errorMessage =
        (payload && typeof payload === 'object' && 'error' in payload && typeof payload.error === 'string' && payload.error) ||
        'Failed to login to management API'
      throw new Error(errorMessage)
    }

    const token =
      payload &&
      typeof payload === 'object' &&
      'data' in payload &&
      payload.data &&
      typeof payload.data === 'object' &&
      'access_token' in payload.data &&
      typeof payload.data.access_token === 'string'
        ? payload.data.access_token
        : ''

    if (!token) throw new Error('Management API login did not return an access token')
    cachedToken = token
    return token
  })()

  try {
    return await loginPromise
  } finally {
    loginPromise = null
  }
}

async function getManagementToken(forceRefresh = false): Promise<string> {
  if (!forceRefresh && cachedToken) return cachedToken
  if (MANAGEMENT_API_USERNAME && MANAGEMENT_API_PASSWORD) {
    return loginForToken()
  }
  if (cachedToken) return cachedToken
  throw new Error('No management API token or login credentials configured')
}

async function doFetch(path: string, init: RequestInit, forceRefresh = false): Promise<Response> {
  const headers = new Headers(init.headers)
  const token = await getManagementToken(forceRefresh)

  // Keep the auth value exactly as configured/issued. The Flask backend expects
  // the raw Flask-Security token rather than forcing a Bearer prefix.
  headers.set('Authorization', token)
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  return fetch(`${MANAGEMENT_API_URL}${path}`, { ...init, headers })
}

export async function mgmtFetch(path: string, init: RequestInit = {}): Promise<Response> {
  let resp = await doFetch(path, init, false)
  if (
    resp.status === 401 &&
    MANAGEMENT_API_USERNAME &&
    MANAGEMENT_API_PASSWORD &&
    cachedToken
  ) {
    resp = await doFetch(path, init, true)
  }
  return resp
}

export async function mgmtFetchJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await mgmtFetch(path, init)
  const payload = await resp.json().catch(() => null)

  if (!resp.ok) {
    const errorMessage =
      payload &&
      typeof payload === 'object' &&
      'error' in payload &&
      typeof payload.error === 'string'
        ? payload.error
        : `Management API request failed (${resp.status})`
    throw new Error(errorMessage)
  }

  return unwrapPayload<T>(payload)
}
