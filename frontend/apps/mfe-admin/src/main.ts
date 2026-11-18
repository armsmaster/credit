// Standalone dev entry (npm run dev). In production the shell loads ./manifest via Module Federation.
import * as Icons from '@element-plus/icons-vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import ru from 'element-plus/es/locale/lang/ru'
import { createApp, h } from 'vue'
import { createRouter, createWebHashHistory, RouterView } from 'vue-router'

import { createPlatform, setPlatform } from '@cds/platform'

import manifest from './manifest'

const platform = await createPlatform({ keycloak: { url: '/auth', realm: 'cds', clientId: 'cds-web' } })
setPlatform(platform)

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: manifest.basePath },
    { path: manifest.basePath, children: manifest.routes },
  ],
})

const app = createApp({ render: () => h(RouterView) })
for (const [name, component] of Object.entries(Icons)) app.component(name, component)
app.use(ElementPlus, { locale: ru }).use(router).mount('#app')
