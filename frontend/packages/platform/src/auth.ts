/**
 * Session handling for our own login page.
 *
 * The admin service talks to Keycloak; the browser only sees
 *  - the access token (kept in memory, ~5 min, refreshed automatically),
 *  - an httpOnly refresh-token cookie it cannot read.
 * No Web Crypto API is needed, so this works over plain HTTP on any host name.
 */
import { ApiError, createHttp } from './http'
import type { Platform, Role, UserInfo } from './index'

const AUTH_API = '/api/admin/public/auth'
const PREFS_API = '/api/admin/me/preferences'

interface TokenResponse {
  access_token: string
  expires_in: number
}

const anonymous = createHttp()

function decodeJwt(token: string): Record<string, any> {
  const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
  const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4)
  const bytes = Uint8Array.from(atob(padded), (c) => c.charCodeAt(0))
  return JSON.parse(new TextDecoder().decode(bytes))
}

class Session {
  private accessToken = ''
  private expiresAt = 0
  private pending: Promise<string> | null = null

  /** Exchange the refresh cookie for a fresh access token (concurrent calls share one request). */
  refresh(): Promise<string> {
    if (!this.pending) {
      this.pending = anonymous
        .post<TokenResponse>(`${AUTH_API}/refresh`)
        .then((t) => {
          this.accessToken = t.access_token
          this.expiresAt = Date.now() + t.expires_in * 1000
          return t.access_token
        })
        .finally(() => {
          this.pending = null
        })
    }
    return this.pending
  }

  async token(): Promise<string> {
    if (this.accessToken && Date.now() < this.expiresAt - 30_000) return this.accessToken
    try {
      return await this.refresh()
    } catch (e) {
      // session expired (idle timeout, blocked user, logout in another tab): back to the login page
      if (e instanceof ApiError && e.status === 401) window.location.reload()
      throw e
    }
  }

  get claims(): Record<string, any> {
    return decodeJwt(this.accessToken)
  }
}

/** Log in; on success the session cookie is set (reload or call createPlatform afterwards). */
export async function login(username: string, password: string): Promise<void> {
  await anonymous.post(`${AUTH_API}/login`, { username, password })
}

/** Replace a temporary password (first login of e.g. the bootstrap administrator) and log in. */
export async function firstLogin(username: string, password: string, newPassword: string): Promise<void> {
  await anonymous.post(`${AUTH_API}/first-login`, { username, password, new_password: newPassword })
}

/** Restore the session from the cookie and build the Platform; null when the user is not logged in. */
export async function createPlatform(): Promise<Platform | null> {
  const session = new Session()
  try {
    await session.refresh()
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return null
    throw e
  }
  const c = session.claims
  const roles = ((c.realm_access?.roles || []) as string[]).filter((r) => r.startsWith('cds-')) as Role[]
  const user: UserInfo = {
    id: c.sub,
    username: c.preferred_username,
    name: c.name || c.preferred_username,
    email: c.email || '',
    roles,
  }
  const token = () => session.token()
  const http = createHttp(token)

  return {
    user,
    hasRole: (role: Role) => roles.includes(role),
    token,
    logout: () => {
      anonymous
        .post(`${AUTH_API}/logout`)
        .catch(() => undefined)
        .finally(() => window.location.assign('/'))
    },
    http,
    preferences: {
      async get<T>(key: string) {
        try {
          const res = await http.get<{ value: T | null }>(`${PREFS_API}/${encodeURIComponent(key)}`)
          return res.value
        } catch {
          return null // preferences are a convenience: never break a page because of them
        }
      },
      async set<T>(key: string, value: T) {
        await http.put(`${PREFS_API}/${encodeURIComponent(key)}`, { value }).catch(() => undefined)
      },
    },
  }
}
