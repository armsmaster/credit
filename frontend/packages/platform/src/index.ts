/**
 * Contract between the shell (host) and microfrontends.
 *
 * The shell authenticates the user (own login page, see auth.ts) and publishes a Platform object;
 * every microfrontend reads it with getPlatform(). A microfrontend exposes a
 * `./manifest` module (MfeManifest) through Module Federation.
 */
import type { RouteRecordRaw } from 'vue-router'

import { ApiError } from './http'

export { ApiError, createHttp, type Http } from './http'
export { createPlatform, firstLogin, login } from './auth'
export * from './format'

export enum Role {
  Viewer = 'cds-viewer',
  Operator = 'cds-operator',
  Admin = 'cds-admin',
}

export const ROLE_TITLES: Record<Role, string> = {
  [Role.Viewer]: 'Просмотр',
  [Role.Operator]: 'Оператор',
  [Role.Admin]: 'Администратор',
}

export interface UserInfo {
  id: string
  username: string
  name: string
  email: string
  roles: Role[]
}

export interface Preferences {
  get<T>(key: string): Promise<T | null>
  set<T>(key: string, value: T): Promise<void>
}

export interface Platform {
  user: UserInfo
  hasRole(role: Role): boolean
  token(): Promise<string>
  logout(): void
  http: import('./http').Http
  preferences: Preferences
}

export interface MenuItem {
  title: string
  path: string
  icon?: string // name of an @element-plus/icons-vue component
  roles?: Role[]
}

export interface MfeManifest {
  name: string
  title: string
  basePath: string // e.g. "/curves"
  routes: RouteRecordRaw[] // relative to basePath
  menu: MenuItem[]
}

const GLOBAL_KEY = '__CDS_PLATFORM__'

export function setPlatform(platform: Platform): void {
  ;(window as unknown as Record<string, unknown>)[GLOBAL_KEY] = platform
}

export function getPlatform(): Platform {
  const p = (window as unknown as Record<string, unknown>)[GLOBAL_KEY] as Platform | undefined
  if (!p) throw new Error('Platform is not initialised: microfrontend is running outside the shell')
  return p
}

export function canSee(item: { roles?: Role[] }, platform: Platform): boolean {
  return !item.roles || item.roles.some((r) => platform.hasRole(r))
}

function filenameFrom(disposition: string | null): string | null {
  const m = disposition?.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
  return m ? decodeURIComponent(m[1]) : null
}

/** Download a file from an API endpoint with the user's token (GET, or POST with a JSON body). */
export async function downloadFile(url: string, options: { method?: 'GET' | 'POST'; body?: unknown; filename?: string } = {}): Promise<void> {
  const token = await getPlatform().token()
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(url, {
    method: options.method ?? (options.body !== undefined ? 'POST' : 'GET'),
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  })
  if (!res.ok) {
    const data = await res.json().catch(() => null)
    throw new ApiError(res.status, typeof data?.detail === 'string' ? data.detail : `Ошибка ${res.status}`, data?.code, data)
  }
  const blob = await res.blob()
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = options.filename ?? filenameFrom(res.headers.get('Content-Disposition')) ?? 'download'
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(link.href), 10000)
}
