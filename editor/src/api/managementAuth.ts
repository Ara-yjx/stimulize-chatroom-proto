import {
  MANAGEMENT_API_PASSWORD,
  MANAGEMENT_API_TOKEN,
  MANAGEMENT_API_URL,
  MANAGEMENT_API_USERNAME,
} from '../config'

const TOKEN_STORAGE_KEY = 'stimulize.editor.managementToken'
const USERNAME_STORAGE_KEY = 'stimulize.editor.managementUsername'

function loadStoredToken(): string {
  if (typeof window === 'undefined') return ''
  try {
    return window.sessionStorage.getItem(TOKEN_STORAGE_KEY) ?? ''
  } catch {
    return ''
  }
}

function loadStoredUsername(): string {
  if (typeof window === 'undefined') return ''
  try {
    return window.sessionStorage.getItem(USERNAME_STORAGE_KEY) ?? ''
  } catch {
    return ''
  }
}

function persistToken(token: string): void {
  if (typeof window === 'undefined') return
  try {
    if (token) {
      window.sessionStorage.setItem(TOKEN_STORAGE_KEY, token)
    } else {
      window.sessionStorage.removeItem(TOKEN_STORAGE_KEY)
    }
  } catch {
    // Ignore storage failures and keep using in-memory auth.
  }
}

function persistUsername(username: string): void {
  if (typeof window === 'undefined') return
  try {
    if (username) {
      window.sessionStorage.setItem(USERNAME_STORAGE_KEY, username)
    } else {
      window.sessionStorage.removeItem(USERNAME_STORAGE_KEY)
    }
  } catch {
    // Ignore storage failures and keep using in-memory auth.
  }
}

let cachedToken = loadStoredToken() || MANAGEMENT_API_TOKEN
let cachedUsername = MANAGEMENT_API_USERNAME
let cachedPassword = MANAGEMENT_API_PASSWORD
let authenticatedUsername = loadStoredUsername()
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
    persistToken(token)
    authenticatedUsername = cachedUsername
    persistUsername(authenticatedUsername)
    return token
  })()

  try {
    return await loginPromise
  } finally {
    loginPromise = null
  }
}

export async function getManagementToken(forceRefresh = false): Promise<string> {
  if (!forceRefresh && cachedToken) return cachedToken
  if (cachedUsername && cachedPassword) {
    return loginForToken()
  }
  if (cachedToken) return cachedToken
  throw new Error('No management API token or login credentials configured')
}

export function hasRefreshableCredentials(): boolean {
  return Boolean(cachedUsername && cachedPassword)
}

export function hasManagementToken(): boolean {
  return Boolean(cachedToken)
}

export function getAuthenticatedUsername(): string {
  return authenticatedUsername
}

export async function loginWithCredentials(username: string, password: string): Promise<void> {
  cachedUsername = username.trim()
  cachedPassword = password
  cachedToken = ''
  persistToken('')
  authenticatedUsername = ''
  persistUsername('')
  await loginForToken()
}

export function logoutManagement(): void {
  cachedToken = ''
  persistToken('')
  authenticatedUsername = ''
  persistUsername('')
}
