<template>
  <el-container class="shell">
    <el-aside width="236px" class="shell-aside">
      <router-link to="/" class="brand">
        <img src="/favicon.svg" alt="" />
        <span>CDS Pricing</span>
      </router-link>
      <el-menu :default-active="activePath" router class="shell-menu">
        <el-menu-item index="/">
          <el-icon><HomeFilled /></el-icon>
          <span>Главная</span>
        </el-menu-item>
        <el-menu-item-group v-for="m in sections" :key="m.name" :title="m.title">
          <el-menu-item v-for="item in m.items" :key="item.path" :index="item.path">
            <el-icon v-if="item.icon"><component :is="item.icon" /></el-icon>
            <span>{{ item.title }}</span>
          </el-menu-item>
        </el-menu-item-group>
      </el-menu>
      <div v-if="failures.length" class="remote-failures">
        <el-alert
          v-for="f in failures"
          :key="f.name"
          type="error"
          :closable="false"
          :title="`Модуль «${f.name}» недоступен`"
          show-icon
        />
      </div>
    </el-aside>
    <el-container>
      <el-header class="shell-header">
        <div class="header-title">{{ currentTitle }}</div>
        <el-dropdown trigger="click">
          <span class="user">
            <el-avatar :size="28">{{ initials }}</el-avatar>
            <span class="user-name">{{ platform.user.name }}</span>
            <el-tag size="small" effect="plain">{{ roleTitle }}</el-tag>
            <el-icon><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item disabled>{{ platform.user.username }} · {{ platform.user.email }}</el-dropdown-item>
              <el-dropdown-item divided @click="platform.logout()">Выйти</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-main class="shell-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, inject } from 'vue'
import { useRoute } from 'vue-router'

import { canSee, getPlatform, ROLE_TITLES, Role, type MfeManifest } from '@cds/platform'

import type { RemoteFailure } from './remotes'

const platform = getPlatform()
const manifests = inject<MfeManifest[]>('manifests', [])
const failures = inject<RemoteFailure[]>('remoteFailures', [])
const route = useRoute()

const sections = computed(() =>
  manifests
    .map((m) => ({
      name: m.name,
      title: m.title,
      items: m.menu
        .filter((item) => canSee(item, platform))
        .map((item) => ({ ...item, path: (m.basePath + '/' + item.path).replace(/\/+$/, '').replace(/\/+/g, '/') })),
    }))
    .filter((s) => s.items.length > 0),
)

const allItems = computed(() => sections.value.flatMap((s) => s.items))

const activePath = computed(() => {
  const candidates = allItems.value.filter((i) => route.path === i.path || route.path.startsWith(i.path + '/'))
  candidates.sort((a, b) => b.path.length - a.path.length)
  return candidates[0]?.path ?? route.path
})

const currentTitle = computed(() => allItems.value.find((i) => i.path === activePath.value)?.title ?? '')

const roleTitle = computed(() => {
  for (const r of [Role.Admin, Role.Operator, Role.Viewer]) if (platform.hasRole(r)) return ROLE_TITLES[r]
  return 'Нет роли'
})

const initials = computed(() =>
  platform.user.name
    .split(/\s+/)
    .map((p) => p[0])
    .join('')
    .slice(0, 2)
    .toUpperCase(),
)
</script>
