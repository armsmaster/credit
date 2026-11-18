import {
  __federation_method_getRemote as getRemote,
  __federation_method_setRemote as setRemote,
  __federation_method_unwrapDefault as unwrapDefault,
} from 'virtual:__federation__'

import type { MfeManifest } from '@cds/platform'

export interface RemoteConfig {
  name: string
  entry: string
}

export interface RemoteFailure {
  name: string
  error: string
}

/**
 * Load `./manifest` of every microfrontend listed in config.json.
 * A broken or unavailable microfrontend must not take the whole shell down.
 */
export async function loadRemotes(remotes: RemoteConfig[]): Promise<{ manifests: MfeManifest[]; failures: RemoteFailure[] }> {
  const results = await Promise.allSettled(
    remotes.map(async (r) => {
      setRemote(r.name, { url: () => Promise.resolve(r.entry), format: 'esm', from: 'vite' })
      const module = await getRemote(r.name, './manifest')
      return (await unwrapDefault(module)) as MfeManifest
    }),
  )
  const manifests: MfeManifest[] = []
  const failures: RemoteFailure[] = []
  results.forEach((res, i) => {
    if (res.status === 'fulfilled') manifests.push(res.value)
    else {
      console.error(`Microfrontend ${remotes[i].name} failed to load`, res.reason)
      failures.push({ name: remotes[i].name, error: String(res.reason) })
    }
  })
  return { manifests, failures }
}
