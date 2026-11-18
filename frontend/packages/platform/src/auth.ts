import Keycloak from 'keycloak-js'

import { createHttp } from './http'
import type { Platform, Role, UserInfo } from './index'

export interface PlatformConfig {
  keycloak: { url: string; realm: string; clientId: string }
  preferencesApi?: string
}

/** Log the user in (redirects to Keycloak if needed) and build the Platform object. */
export async function createPlatform(config: PlatformConfig): Promise<Platform> {
  const url = config.keycloak.url.startsWith('http') ? config.keycloak.url : window.location.origin + config.keycloak.url
  const kc = new Keycloak({ url, realm: config.keycloak.realm, clientId: config.keycloak.clientId })
  await kc.init({ onLoad: 'login-required', pkceMethod: 'S256', checkLoginIframe: false })

  const parsed = (kc.tokenParsed || {}) as Record<string, any>
  const roles = ((parsed.realm_access?.roles || []) as string[]).filter((r) => r.startsWith('cds-')) as Role[]
  const user: UserInfo = {
    id: parsed.sub,
    username: parsed.preferred_username,
    name: parsed.name || parsed.preferred_username,
    email: parsed.email || '',
    roles,
  }

  async function token(): Promise<string> {
    try {
      await kc.updateToken(30)
    } catch {
      await kc.login()
    }
    return kc.token as string
  }

  const http = createHttp(token)
  const prefsApi = config.preferencesApi ?? '/api/admin/me/preferences'

  return {
    user,
    hasRole: (role: Role) => roles.includes(role),
    token,
    logout: () => kc.logout({ redirectUri: window.location.origin + '/' }),
    http,
    preferences: {
      async get<T>(key: string) {
        try {
          const res = await http.get<{ value: T | null }>(`${prefsApi}/${encodeURIComponent(key)}`)
          return res.value
        } catch {
          return null // preferences are a convenience: never break a page because of them
        }
      },
      async set<T>(key: string, value: T) {
        await http.put(`${prefsApi}/${encodeURIComponent(key)}`, { value }).catch(() => undefined)
      },
    },
  }
}
