<template>
  <div class="home">
    <h2>Здравствуйте, {{ platform.user.name }}</h2>
    <p class="muted">Система прайсинга CDS. Выберите раздел:</p>
    <div class="cards">
      <template v-for="m in manifests" :key="m.name">
        <router-link
          v-for="item in m.menu.filter((i) => canSee(i, platform))"
          :key="m.name + item.path"
          :to="(m.basePath + '/' + item.path).replace(/\/+$/, '')"
          class="card-link"
        >
          <el-card shadow="hover">
            <div class="card-body">
              <el-icon :size="26"><component :is="item.icon || 'Document'" /></el-icon>
              <div>
                <div class="card-title">{{ item.title }}</div>
                <div class="muted">{{ m.title }}</div>
              </div>
            </div>
          </el-card>
        </router-link>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { inject } from 'vue'

import { canSee, getPlatform, type MfeManifest } from '@cds/platform'

const platform = getPlatform()
const manifests = inject<MfeManifest[]>('manifests', [])
</script>

<style scoped>
.home h2 {
  margin-top: 0;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 16px;
}
.card-link {
  text-decoration: none;
}
.card-body {
  display: flex;
  align-items: center;
  gap: 14px;
  color: var(--cds-brand);
}
.card-title {
  font-weight: 600;
  color: #1d2129;
}
</style>
