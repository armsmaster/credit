import * as Icons from '@element-plus/icons-vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import ru from 'element-plus/es/locale/lang/ru'
import { createApp, type Component } from 'vue'

import { createPlatform, setPlatform } from '@cds/platform'

import App from './App.vue'
import InviteApp from './InviteApp.vue'
import LoginApp from './LoginApp.vue'
import { loadRemotes, type RemoteConfig } from './remotes'
import { buildRouter } from './router'
import './styles.css'

interface ShellConfig {
  remotes: RemoteConfig[]
}

function mountStandalone(component: Component): void {
  createApp(component).use(ElementPlus, { locale: ru }).mount('#app')
}

async function bootstrap(): Promise<void> {
  // The first-login page is public: the invitation token is the credential.
  if (window.location.pathname.startsWith('/invite/')) {
    mountStandalone(InviteApp)
    return
  }

  const [config, platform] = await Promise.all([
    fetch('/config.json', { cache: 'no-cache' }).then((r) => r.json() as Promise<ShellConfig>),
    createPlatform(),
  ])
  if (!platform) {
    mountStandalone(LoginApp) // no session: own login page at the requested URL
    return
  }
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
