<template>
  <div>
    <div class="page-toolbar">
      <el-button :type="filters.tag === BACKDATED ? 'warning' : 'default'" @click="toggleBackdated">
        <el-icon><Clock /></el-icon>&nbsp;Изменения задним числом
      </el-button>
      <el-date-picker
        v-model="range"
        type="datetimerange"
        start-placeholder="С"
        end-placeholder="По"
        :shortcuts="shortcuts"
        style="width: 360px"
        @change="reload"
      />
      <el-select v-model="filters.service" placeholder="Сервис" clearable style="width: 130px" @change="reload">
        <el-option v-for="s in facets.services" :key="s" :value="s" />
      </el-select>
      <el-select v-model="filters.entity_type" placeholder="Объект" clearable style="width: 160px" @change="reload">
        <el-option v-for="s in facets.entity_types" :key="s" :value="s" :label="ENTITY_LABELS[s] || s" />
      </el-select>
      <el-select v-model="filters.action" placeholder="Действие" clearable style="width: 160px" @change="reload">
        <el-option v-for="s in facets.actions" :key="s" :value="s" :label="ACTION_LABELS[s] || s" />
      </el-select>
      <el-input v-model="filters.actor" placeholder="Пользователь" clearable style="width: 150px" @change="reload" />
      <el-input v-model="filters.q" placeholder="Поиск по тексту" clearable style="width: 200px" @change="reload">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <div class="spacer" />
      <el-button @click="reload"><el-icon><Refresh /></el-icon></el-button>
    </div>

    <el-table v-loading="loading" :data="items" row-key="id" stripe>
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="details">
            <div class="meta">
              <span>Объект: <b>{{ row.entity_type }}</b> · <span class="mono">{{ row.entity_id }}</span></span>
              <span v-if="row.request_id">Request ID: <span class="mono">{{ row.request_id }}</span></span>
            </div>
            <el-table v-if="row.changes && Object.keys(row.changes).length" :data="changeRows(row)" size="small" border>
              <el-table-column prop="field" label="Поле" width="200" />
              <el-table-column label="Было">
                <template #default="{ row: c }"><pre class="json">{{ show(c.old) }}</pre></template>
              </el-table-column>
              <el-table-column label="Стало">
                <template #default="{ row: c }"><pre class="json">{{ show(c.new) }}</pre></template>
              </el-table-column>
            </el-table>
            <span v-else class="muted">Нет детализации изменений</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="Время" width="170">
        <template #default="{ row }">{{ formatDateTime(row.occurred_at) }}</template>
      </el-table-column>
      <el-table-column prop="service" label="Сервис" width="90" />
      <el-table-column label="Действие" width="190">
        <template #default="{ row }">{{ ACTION_LABELS[row.action] || row.action }}</template>
      </el-table-column>
      <el-table-column prop="actor_username" label="Пользователь" width="150" />
      <el-table-column prop="summary" label="Описание" min-width="320" />
      <el-table-column label="Теги" width="190">
        <template #default="{ row }">
          <el-tag v-for="t in row.tags" :key="t" :type="t === BACKDATED ? 'warning' : 'info'" size="small">
            {{ TAG_LABELS[t] || t }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      class="pager"
      layout="total, sizes, prev, pager, next"
      :page-sizes="[25, 50, 100, 200]"
      :total="total"
      @current-change="load"
      @size-change="reload"
    />
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import { formatDateTime } from '@cds/platform'

import { api, type AuditEvent } from '../api'

const BACKDATED = 'backdated'
const TAG_LABELS: Record<string, string> = { backdated: 'изменение задним числом' }
const ENTITY_LABELS: Record<string, string> = {
  curve: 'Кривая',
  curve_definition: 'Параметры кривой',
  currency: 'Валюта',
  user: 'Пользователь',
  schedule: 'Расписание',
}
const ACTION_LABELS: Record<string, string> = {
  create: 'Создание',
  update: 'Изменение',
  delete: 'Удаление',
  state_save: 'Сохранение текущей кривой',
  history_archive: 'Перенос в историю',
  history_edit: 'Изменение истории',
  role_change: 'Смена роли',
  block: 'Блокировка',
  unblock: 'Разблокировка',
  invite_issue: 'Выпуск ссылки входа',
  invite_accept: 'Первый вход по ссылке',
}

const route = useRoute()
const items = ref<AuditEvent[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const loading = ref(false)
const range = ref<[Date, Date] | null>(null)
const facets = reactive({ services: [] as string[], entity_types: [] as string[], actions: [] as string[] })
const filters = reactive({
  service: '',
  entity_type: '',
  action: '',
  actor: '',
  q: '',
  tag: (route.query.tag as string) || '',
  entity_id: (route.query.entity_id as string) || '',
})

const shortcuts = [
  { text: 'Сегодня', value: () => [new Date(new Date().setHours(0, 0, 0, 0)), new Date()] },
  { text: '7 дней', value: () => [new Date(Date.now() - 7 * 864e5), new Date()] },
  { text: '30 дней', value: () => [new Date(Date.now() - 30 * 864e5), new Date()] },
]

function changeRows(row: AuditEvent) {
  return Object.entries(row.changes || {}).map(([field, c]) => ({ field, old: c?.old, new: c?.new }))
}

function show(v: unknown): string {
  if (v === null || v === undefined) return '—'
  return typeof v === 'string' ? v : JSON.stringify(v, null, 2)
}

function toggleBackdated() {
  filters.tag = filters.tag === BACKDATED ? '' : BACKDATED
  reload()
}

function reload() {
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const res = await api.audit.list({
      ...filters,
      date_from: range.value?.[0]?.toISOString(),
      date_to: range.value?.[1]?.toISOString(),
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    items.value = res.items
    total.value = res.total
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  load()
  Object.assign(facets, await api.audit.facets().catch(() => ({})))
})
</script>

<style scoped>
.details {
  padding: 8px 48px 16px;
  display: grid;
  gap: 8px;
}
.meta {
  display: flex;
  gap: 24px;
  font-size: 13px;
}
.muted {
  color: var(--el-text-color-secondary);
}
.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
.el-tag + .el-tag {
  margin-left: 4px;
}
</style>
