/**
 * Thin wrapper around `fetch` that targets the management API and attaches
 * the bearer token from build-time env. See docs/low-level-design.md
 * "Editor → Management API".
 */
import { MANAGEMENT_API_URL } from '../config'
import { getManagementToken, hasRefreshableCredentials, logoutManagement } from './managementAuth'

export class ManagementAuthExpiredError extends Error {
  status: number

  constructor(message = '登录已过期') {
    super(message)
    this.name = 'ManagementAuthExpiredError'
    this.status = 401
  }
}

export function isManagementAuthExpiredError(error: unknown): error is ManagementAuthExpiredError {
  return error instanceof ManagementAuthExpiredError
}

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
  if (resp.status === 401 && hasRefreshableCredentials()) {
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
    if (resp.status === 401) {
      logoutManagement()
      throw new ManagementAuthExpiredError('登录已过期')
    }
    throw new Error(errorMessage)
  }

  return unwrapPayload<T>(payload)
}
