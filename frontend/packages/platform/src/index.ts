/**
 * Contract between the shell (host) and microfrontends.
 *
 * The shell authenticates the user (Keycloak) and publishes a Platform object;
 * every microfrontend reads it with getPlatform(). A microfrontend exposes a
 * `./manifest` module (MfeManifest) through Module Federation.
 */
import type { RouteRecordRaw } from 'vue-router'

export { ApiError, createHttp, type Http } from './http'
export { createPlatform, type PlatformConfig } from './auth'
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
