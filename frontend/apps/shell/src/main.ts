import * as Icons from '@element-plus/icons-vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import ru from 'element-plus/es/locale/lang/ru'
import { createApp } from 'vue'

import { createPlatform, setPlatform } from '@cds/platform'

import App from './App.vue'
import InviteApp from './InviteApp.vue'
import { loadRemotes, type RemoteConfig } from './remotes'
import { buildRouter } from './router'
import './styles.css'

interface ShellConfig {
  keycloak: { url: string; realm: string; clientId: string }
  remotes: RemoteConfig[]
}

async function bootstrap(): Promise<void> {
  const config: ShellConfig = await fetch('/config.json', { cache: 'no-cache' }).then((r) => r.json())

  // The first-login page is public: the invitation token is the credential.
  if (window.location.pathname.startsWith('/invite/')) {
    createApp(InviteApp).use(ElementPlus, { locale: ru }).mount('#app')
    return
  }

  const platform = await createPlatform({ keycloak: config.keycloak })
  setPlatform(platform)

  const { manifests, failures } = await loadRemotes(config.remotes)
  const router = buildRouter(manifests, platform)

  const app = createApp(App)
  app.provide('manifests', manifests)
  app.provide('remoteFailures', failures)
  app.use(ElementPlus, { locale: ru })
  for (const [name, component] of Object.entries(Icons)) app.component(name, component)
  app.use(router)
  app.mount('#app')
}

bootstrap().catch((err) => {
  console.error(err)
  const el = document.getElementById('app')
  if (el) el.innerHTML = `<div style="padding:48px;font-family:sans-serif"><h2>Не удалось запустить приложение</h2><pre>${String(err)}</pre></div>`
})
