import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

class MemoryStorage implements Storage {
  private store = new Map<string, string>()

  get length(): number {
    return this.store.size
  }

  clear(): void {
    this.store.clear()
  }

  getItem(key: string): string | null {
    return this.store.has(key) ? this.store.get(key) ?? null : null
  }

  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null
  }

  removeItem(key: string): void {
    this.store.delete(key)
  }

  setItem(key: string, value: string): void {
    this.store.set(key, value)
  }
}

const AUTH_STORAGE_KEY = 'stimulize.editor.managementAuth'

async function loadAuthModule() {
  vi.resetModules()
  vi.doMock('../../config', () => ({
    MANAGEMENT_API_PASSWORD: '',
    MANAGEMENT_API_TOKEN: '',
    MANAGEMENT_API_URL: 'https://example.test',
    MANAGEMENT_API_USERNAME: '',
  }))
  return import('../managementAuth')
}

describe('managementAuth local persistence', () => {
  let storage: MemoryStorage

  beforeEach(() => {
    storage = new MemoryStorage()
    vi.stubGlobal('window', { localStorage: storage })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    vi.resetModules()
  })

  it('auto-loads a non-expired token from localStorage on module init', async () => {
    const now = 1_700_000_000_000
    vi.spyOn(Date, 'now').mockReturnValue(now)
    storage.setItem(AUTH_STORAGE_KEY, JSON.stringify({
      token: 'saved-token',
      username: 'saved-user',
      tokenCreatedAt: now - 1_000,
      tokenExpiresAt: now + 60_000,
    }))

    const auth = await loadAuthModule()

    expect(auth.hasManagementToken()).toBe(true)
    expect(auth.getAuthenticatedUsername()).toBe('saved-user')
    await expect(auth.getManagementToken()).resolves.toBe('saved-token')
  })

  it('drops expired stored auth during module init', async () => {
    const now = 1_700_000_000_000
    vi.spyOn(Date, 'now').mockReturnValue(now)
    storage.setItem(AUTH_STORAGE_KEY, JSON.stringify({
      token: 'expired-token',
      username: 'expired-user',
      tokenCreatedAt: now - 10_000,
      tokenExpiresAt: now - 1,
    }))

    const auth = await loadAuthModule()

    expect(auth.hasManagementToken()).toBe(false)
    expect(auth.getAuthenticatedUsername()).toBe('')
    expect(storage.getItem(AUTH_STORAGE_KEY)).toBeNull()
  })

  it('persists login token with created and expiry timestamps in localStorage', async () => {
    const now = 1_700_000_000_000
    vi.spyOn(Date, 'now').mockReturnValue(now)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        data: {
          access_token: 'fresh-token',
        },
      }),
    }))

    const auth = await loadAuthModule()
    await auth.loginWithCredentials(' researcher ', 'secret')

    expect(auth.hasManagementToken()).toBe(true)
    expect(auth.getAuthenticatedUsername()).toBe('researcher')

    const persisted = JSON.parse(storage.getItem(AUTH_STORAGE_KEY) ?? 'null') as {
      token: string
      username: string
      tokenCreatedAt: number
      tokenExpiresAt: number
    } | null

    expect(persisted).toEqual({
      token: 'fresh-token',
      username: 'researcher',
      tokenCreatedAt: now,
      tokenExpiresAt: now + 3 * 60 * 60 * 1000,
    })
  })
})
