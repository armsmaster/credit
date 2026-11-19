<template>
  <div>
    <div class="page-toolbar">
      <h2>Эмитенты</h2>
      <span class="muted">Облигации отслеживаемых эмитентов сканируются на Мосбирже ежедневно</span>
      <div class="spacer" />
      <el-button @click="load"><el-icon><Refresh /></el-icon>&nbsp;Обновить</el-button>
      <el-button v-if="isOperator" type="primary" @click="openSearch"><el-icon><Plus /></el-icon>&nbsp;Добавить эмитента</el-button>
    </div>

    <el-table v-loading="loading" :data="items" stripe>
      <el-table-column label="Эмитент" min-width="260">
        <template #default="{ row }">
          <b>{{ row.short_title || row.title }}</b>
          <div class="muted small">{{ row.title }}</div>
        </template>
      </el-table-column>
      <el-table-column prop="inn" label="ИНН" width="120" />
      <el-table-column label="Облигации" width="290">
        <template #default="{ row }">
          <router-link :to="{ path: '/credit/bonds', query: { issuer: row.emitter_id } }" class="counts">
            <span>всего {{ row.bonds_total }}</span>
            <el-tag size="small" type="info">подходят {{ row.bonds_eligible }}</el-tag>
            <el-tag size="small" type="success">рекоменд. {{ row.bonds_recommended }}</el-tag>
            <el-tag v-if="row.bonds_incomplete" size="small" type="warning">неполные {{ row.bonds_incomplete }}</el-tag>
          </router-link>
        </template>
      </el-table-column>
      <el-table-column label="Кривые" min-width="140">
        <template #default="{ row }">
          <router-link v-for="c in row.curves" :key="c" :to="`/credit/${encodeURIComponent(c)}`" class="curve-link">{{ c }}</router-link>
          <span v-if="!row.curves.length" class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="Скан" width="210">
        <template #default="{ row }">
          <el-tag :type="STATUS[row.scan_status ?? '']?.type ?? 'info'" size="small">{{ STATUS[row.scan_status ?? '']?.label ?? '—' }}</el-tag>
          <span class="muted small"> {{ formatDateTime(row.scanned_at) }}</span>
          <el-tooltip v-if="row.scan_error" :content="row.scan_error" placement="top">
            <el-icon color="var(--el-color-danger)" class="err"><WarningFilled /></el-icon>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column v-if="isOperator" label="" width="190" align="right">
        <template #default="{ row }">
          <el-button size="small" @click="scan(row)">Сканировать</el-button>
          <el-button size="small" type="danger" plain :disabled="row.curves.length > 0" @click="remove(row)">
            <el-icon><Delete /></el-icon>
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="searchOpen" title="Добавить эмитента из справочника Мосбиржи" width="720px">
      <el-input v-model="query" placeholder="Название или ИНН, например «ВЭБ» или 7750004150" clearable @keyup.enter="search">
        <template #append><el-button :loading="searching" @click="search">Найти</el-button></template>
      </el-input>
      <el-table :data="found" size="small" class="mt" max-height="420" empty-text="Введите запрос">
        <el-table-column label="Эмитент" min-width="300">
          <template #default="{ row }"><b>{{ row.short_title || row.title }}</b><div class="muted small">{{ row.title }}</div></template>
        </el-table-column>
        <el-table-column prop="inn" label="ИНН" width="120" />
        <el-table-column label="" width="140" align="right">
          <template #default="{ row }">
            <el-tag v-if="row.tracked" type="success" size="small">отслеживается</el-tag>
            <el-button v-else size="small" type="primary" :loading="adding === row.emitter_id" @click="add(row)">Добавить</el-button>
          </template>
        </el-table-column>
      </el-table>
      <p class="muted small">После добавления сразу запускается первичный скан облигаций эмитента (статика, график платежей, история торгов).</p>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import { h, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { formatDateTime, getPlatform, Role } from '@cds/platform'

import { api } from '../api'
import type { IssSearchItem, Issuer } from '../types'

const STATUS: Record<string, { label: string; type: 'success' | 'warning' | 'danger' | 'info' }> = {
  ok: { label: 'готово', type: 'success' },
  queued: { label: 'в очереди', type: 'info' },
  running: { label: 'идёт', type: 'warning' },
  error: { label: 'ошибка', type: 'danger' },
}

const router = useRouter()
const isOperator = getPlatform().hasRole(Role.Operator)
const items = ref<Issuer[]>([])
const loading = ref(false)
const searchOpen = ref(false)
const query = ref('')
const found = ref<IssSearchItem[]>([])
const searching = ref(false)
const adding = ref<number | null>(null)
let timer: number | undefined

async function load() {
  loading.value = true
  try {
    items.value = await api.issuers.list()
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
  clearTimeout(timer)
  if (items.value.some((i) => i.scan_status === 'queued' || i.scan_status === 'running')) timer = window.setTimeout(load, 5000)
}

function openSearch() {
  searchOpen.value = true
}

async function search() {
  searching.value = true
  try {
    found.value = await api.issuers.search(query.value)
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    searching.value = false
  }
}

function taskLink(taskId: string) {
  return h('a', { href: '#', onClick: (ev: Event) => { ev.preventDefault(); router.push({ path: '/admin/tasks', query: { task: taskId } }) } }, 'Открыть задачу')
}

async function add(row: IssSearchItem) {
  adding.value = row.emitter_id
  try {
    const { task_id } = await api.issuers.add(row.emitter_id)
    row.tracked = true
    ElNotification({ title: `${row.short_title || row.title}: запущен скан облигаций`, type: 'success', message: taskLink(task_id) })
    await load()
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    adding.value = null
  }
}

async function scan(row: Issuer) {
  try {
    const { task_id } = await api.issuers.scan(row.emitter_id)
    ElNotification({ title: 'Скан поставлен в очередь', type: 'success', message: taskLink(task_id) })
    await load()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

async function remove(row: Issuer) {
  try {
    await ElMessageBox.confirm(`Перестать отслеживать ${row.short_title || row.title}? Данные его облигаций будут удалены.`, 'Удаление', { type: 'warning' })
    await api.issuers.remove(row.emitter_id)
    await load()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error(e.message)
  }
}

onMounted(load)
onBeforeUnmount(() => clearTimeout(timer))
</script>

<style scoped>
.page-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.page-toolbar h2 {
  margin: 0;
}
.spacer {
  flex: 1;
}
.muted {
  color: var(--el-text-color-secondary);
}
.small {
  font-size: 12px;
}
.counts {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  text-decoration: none;
  color: inherit;
}
.curve-link {
  margin-right: 8px;
}
.err {
  vertical-align: middle;
  margin-left: 4px;
}
.mt {
  margin-top: 12px;
}
</style>
