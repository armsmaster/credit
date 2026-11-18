import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import type { MfeManifest, Platform, Role } from '@cds/platform'

import Layout from './Layout.vue'
import ForbiddenPage from './pages/ForbiddenPage.vue'
import HomePage from './pages/HomePage.vue'
import NotFoundPage from './pages/NotFoundPage.vue'

export function buildRouter(manifests: MfeManifest[], platform: Platform) {
  const children: RouteRecordRaw[] = [{ path: '', name: 'home', component: HomePage }]
  for (const m of manifests) {
    children.push({ path: m.basePath.replace(/^\//, ''), children: m.routes })
  }
  children.push(
    { path: 'forbidden', name: 'forbidden', component: ForbiddenPage },
    { path: ':pathMatch(.*)*', name: 'not-found', component: NotFoundPage },
  )

  const router = createRouter({
    history: createWebHistory(),
    routes: [{ path: '/', component: Layout, children }],
  })

  // Every matched record with meta.roles requires at least one of those roles.
  router.beforeEach((to) => {
    const allowed = to.matched.every((record) => {
      const roles = record.meta.roles as Role[] | undefined
      return !roles || roles.some((r) => platform.hasRole(r))
    })
    if (!allowed) return { name: 'forbidden' }
  })
  return router
}
