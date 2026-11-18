/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<object, object, unknown>
  export default component
}

declare module 'virtual:__federation__' {
  interface RemoteConfig {
    url: string | (() => Promise<string>)
    format: 'esm' | 'systemjs' | 'var'
    from: 'vite' | 'webpack'
  }
  export function __federation_method_setRemote(name: string, config: RemoteConfig): void
  export function __federation_method_getRemote(name: string, exposedPath: string): Promise<unknown>
  export function __federation_method_unwrapDefault(module: unknown): Promise<unknown>
}
