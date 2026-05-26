import {
  MANAGEMENT_API_PASSWORD,
  MANAGEMENT_API_TOKEN,
  MANAGEMENT_API_URL,
  MANAGEMENT_API_USERNAME,
} from '../config'

const AUTH_STORAGE_KEY = 'stimulize.editor.managementAuth'
const TOKEN_TTL_MS = 3 * 60 * 60 * 1000

type StoredManagementAuth = {
  token: string
  username: string
  tokenCreatedAt: number
  tokenExpiresAt: number
}

function clearStoredAuth(): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.removeItem(AUTH_STORAGE_KEY)
  } catch {
    // Ignore storage failures and keep using in-memory auth.
  }
}

function isStoredManagementAuth(value: unknown): value is StoredManagementAuth {
  if (!value || typeof value !== 'object') return false
  const auth = value as Partial<StoredManagementAuth>
  return (
    typeof auth.token === 'string' &&
    typeof auth.username === 'string' &&
    typeof auth.tokenCreatedAt === 'number' &&
    typeof auth.tokenExpiresAt === 'number'
  )
}

function loadStoredAuth(): StoredManagementAuth | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(AUTH_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as unknown
    if (!isStoredManagementAuth(parsed)) {
      clearStoredAuth()
      return null
    }
    if (Date.now() >= parsed.tokenExpiresAt) {
      clearStoredAuth()
      return null
    }
    return parsed
  } catch {
    return null
  }
}

function persistAuth(token: string, username: string): number | null {
  if (!token) {
    clearStoredAuth()
    return null
  }
  const tokenCreatedAt = Date.now()
  const tokenExpiresAt = tokenCreatedAt + TOKEN_TTL_MS
  if (typeof window !== 'undefined') {
    try {
      const auth: StoredManagementAuth = {
        token,
        username,
        tokenCreatedAt,
        tokenExpiresAt,
      }
      window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(auth))
    } catch {
      // Ignore storage failures and keep using in-memory auth.
    }
  }
  return tokenExpiresAt
}

function clearCachedAuth(): void {
  cachedToken = ''
  cachedTokenExpiresAt = null
  authenticatedUsername = ''
  clearStoredAuth()
}

function hasUsableToken(): boolean {
  if (!cachedToken) return false
  if (cachedTokenExpiresAt !== null && Date.now() >= cachedTokenExpiresAt) {
    clearCachedAuth()
    return false
  }
  return true
}

const storedAuth = loadStoredAuth()

let cachedToken = storedAuth?.token || MANAGEMENT_API_TOKEN
let cachedTokenExpiresAt = storedAuth?.tokenExpiresAt ?? null
let cachedUsername = MANAGEMENT_API_USERNAME
let cachedPassword = MANAGEMENT_API_PASSWORD
let authenticatedUsername = storedAuth?.username ?? ''
let loginPromise: Promise<string> | null = null

export async function loginForToken(): Promise<string> {
  if (!cachedUsername || !cachedPassword) {
    throw new Error('Management API credentials are not configured')
  }
  if (loginPromise) return loginPromise

  loginPromise = (async () => {
    const resp = await fetch(`${MANAGEMENT_API_URL}/api/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: cachedUsername,
        password: cachedPassword,
      }),
    })

    const payload = await resp.json().catch(() => ({}))
    if (!resp.ok) {
      const errorMessage =
        (payload &&
          typeof payload === 'object' &&
          'error' in payload &&
          typeof payload.error === 'string' &&
          payload.error) ||
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
    authenticatedUsername = cachedUsername
    cachedTokenExpiresAt = persistAuth(token, authenticatedUsername)
    return token
  })()

  try {
    return await loginPromise
  } finally {
    loginPromise = null
  }
}

export async function getManagementToken(forceRefresh = false): Promise<string> {
  if (!forceRefresh && hasUsableToken()) return cachedToken
  if (cachedUsername && cachedPassword) {
    return loginForToken()
  }
  if (hasUsableToken()) return cachedToken
  throw new Error('No management API token or login credentials configured')
}

export function hasRefreshableCredentials(): boolean {
  return Boolean(cachedUsername && cachedPassword)
}

export function hasManagementToken(): boolean {
  return hasUsableToken()
}

export function getAuthenticatedUsername(): string {
  hasUsableToken()
  return authenticatedUsername
}

export async function loginWithCredentials(username: string, password: string): Promise<void> {
  cachedUsername = username.trim()
  cachedPassword = password
  clearCachedAuth()
  await loginForToken()
}

export function logoutManagement(): void {
  clearCachedAuth()
}
