<template>
  <div>
    <div class="page-toolbar">
      <h2>Облигации</h2>
      <el-select v-model="issuerId" placeholder="Все эмитенты" clearable filterable style="width: 280px" @change="onIssuer">
        <el-option v-for="i in issuers" :key="i.emitter_id" :value="i.emitter_id" :label="i.short_title || i.title" />
      </el-select>
      <el-radio-group v-model="filter">
        <el-radio-button value="eligible">Подходящие ({{ counts.eligible }})</el-radio-button>
        <el-radio-button value="recommended">Рекомендованные ({{ counts.recommended }})</el-radio-button>
        <el-radio-button value="incomplete">
          <el-badge :value="counts.incomplete" :hidden="!counts.incomplete" type="warning" class="badge">Неполные данные</el-badge>
        </el-radio-button>
        <el-radio-button value="all">Все ({{ counts.all }})</el-radio-button>
      </el-radio-group>
      <el-input v-model="search" placeholder="Поиск по названию, ISIN" clearable style="width: 220px">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <div class="spacer" />
      <el-button @click="load"><el-icon><Refresh /></el-icon></el-button>
    </div>
    <el-alert v-if="filter === 'incomplete' && counts.incomplete" type="warning" :closable="false" show-icon class="mb"
      title="Подходящие по типу бумаги, по которым в ISS не хватает данных для расчёта. Нажмите ✎, чтобы дозаполнить." />
    <el-alert v-if="!loading && !issuers.length" type="info" :closable="false" show-icon class="mb">
      Нет отслеживаемых эмитентов — добавьте их на странице <router-link to="/credit/issuers">«Эмитенты»</router-link>.
    </el-alert>

    <div v-loading="loading">
      <BondsTable :bonds="filtered" :editable="isOperator" :window-days="windowDays" @open="(b) => (openSecid = b.secid)" @edit="(b) => (editing = b)" />
    </div>

    <BondDrawer :secid="openSecid" :editable="isOperator" :version="drawerVersion" @close="openSecid = null" @edit="(b) => (editing = b)" />
    <BondEditDialog :bond="editing" @close="editing = null" @saved="onSaved" />
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getPlatform, Role } from '@cds/platform'

import { api } from '../api'
import BondDrawer from '../components/BondDrawer.vue'
import BondEditDialog from '../components/BondEditDialog.vue'
import BondsTable from '../components/BondsTable.vue'
import type { Bond, Issuer } from '../types'

type Filter = 'eligible' | 'recommended' | 'incomplete' | 'all'

const route = useRoute()
const router = useRouter()
const isOperator = getPlatform().hasRole(Role.Operator)
const issuers = ref<Issuer[]>([])
const bonds = ref<Bond[]>([])
const issuerId = ref<number | null>(route.query.issuer ? Number(route.query.issuer) : null)
const filter = ref<Filter>((route.query.filter as Filter) || 'eligible')
const search = ref('')
const loading = ref(false)
const windowDays = ref(90)
const openSecid = ref<string | null>(null)
const editing = ref<Bond | null>(null)
const drawerVersion = ref(0)

const isIncomplete = (b: Bond) => b.profile.eligible && !b.profile.complete
const counts = computed(() => ({
  all: bonds.value.length,
  eligible: bonds.value.filter((b) => b.profile.eligible).length,
  recommended: bonds.value.filter((b) => b.recommended).length,
  incomplete: bonds.value.filter(isIncomplete).length,
}))
const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return bonds.value.filter((b) => {
    if (filter.value === 'eligible' && !b.profile.eligible) return false
    if (filter.value === 'recommended' && !b.recommended) return false
    if (filter.value === 'incomplete' && !isIncomplete(b)) return false
    if (q && !`${b.shortname} ${b.name} ${b.secid} ${b.isin}`.toLowerCase().includes(q)) return false
    return true
  })
})

async function load() {
  loading.value = true
  try {
    const [iss, list, meta] = await Promise.all([api.issuers.list(), api.bonds.list({ issuer_id: issuerId.value }), api.meta()])
    issuers.value = iss
    bonds.value = list
    windowDays.value = meta.settings.liquidity.window_days
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function onIssuer() {
  load()
}

function onSaved() {
  drawerVersion.value++
  load()
}

watch([issuerId, filter], () => router.replace({ query: { issuer: issuerId.value ?? undefined, filter: filter.value } }))
onMounted(load)
</script>

<style scoped>
.page-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.page-toolbar h2 {
  margin: 0;
}
.spacer {
  flex: 1;
}
.mb {
  margin-bottom: 12px;
}
.badge :deep(.el-badge__content) {
  top: -2px;
}
</style>
