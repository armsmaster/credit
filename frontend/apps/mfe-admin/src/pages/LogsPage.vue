<template>
  <div>
    <div class="page-toolbar">
      <el-radio-group v-model="timeWindow" @change="reload">
        <el-radio-button v-for="w in windows" :key="w.value" :value="w.value">{{ w.label }}</el-radio-button>
      </el-radio-group>
      <el-date-picker
        v-if="timeWindow === 'custom'"
        v-model="range"
        type="datetimerange"
        start-placeholder="С"
        end-placeholder="По"
        style="width: 360px"
        @change="reload"
      />
      <el-select v-model="services" multiple collapse-tags placeholder="Сервисы" clearable style="width: 220px" @change="reload">
        <el-option v-for="s in serviceOptions" :key="s" :value="s" />
      </el-select>
      <el-select v-model="levels" multiple collapse-tags placeholder="Уровень" clearable style="width: 180px" @change="reload">
        <el-option v-for="l in levelOptions" :key="l" :value="l" />
      </el-select>
      <el-input v-model="q" placeholder="Текст (AND, &quot;фраза&quot;, -исключить)" clearable style="width: 280px" @change="reload">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-input v-model="requestId" placeholder="Request ID / Task ID" clearable style="width: 200px" @change="reload" />
      <div class="spacer" />
      <el-switch v-model="live" active-text="Live" />
      <el-button @click="reload"><el-icon><Refresh /></el-icon></el-button>
    </div>
    <p class="muted">Логи хранятся 7 дней (Elasticsearch, политика ILM). Время — локальное.</p>

    <el-table v-loading="loading" :data="items" row-key="id" size="small" class="logs">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="details">
            <pre class="json">{{ row.message }}</pre>
            <template v-if="row.exc_info">
              <b>Traceback</b>
              <pre class="json error-text">{{ row.exc_info }}</pre>
            </template>
            <div class="ids">
              <span v-if="row.request_id">request_id: <el-link type="primary" @click="filterBy(row.request_id)">{{ row.request_id }}</el-link></span>
              <span v-if="row.task_id">task_id: <el-link type="primary" @click="filterBy(row.task_id)">{{ row.task_id }}</el-link></span>
              <span v-if="row.user">user: {{ row.user }}</span>
              <span v-if="row.logger">logger: {{ row.logger }}</span>
            </div>
            <pre v-if="Object.keys(row.extra).length" class="json">{{ JSON.stringify(row.extra, null, 2) }}</pre>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="Время" width="185">
        <template #default="{ row }"><span class="mono">{{ ts(row.timestamp) }}</span></template>
      </el-table-column>
      <el-table-column prop="service" label="Сервис" width="130" />
      <el-table-column label="Уровень" width="95">
        <template #default="{ row }">
          <el-tag v-if="row.level" size="small" :type="levelType(row.level)" effect="plain">{{ row.level }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="Сообщение" min-width="500">
        <template #default="{ row }"><span class="mono msg">{{ row.message }}</span></template>
      </el-table-column>
    </el-table>
    <el-pagination
      v-model:current-page="page"
      class="pager"
      layout="total, prev, pager, next"
      :page-size="pageSize"
      :total="Math.min(total, 10000)"
      @current-change="load"
    />
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api, type LogEntry } from '../api'

const windows = [
  { label: '15 мин', value: '15m', ms: 15 * 60e3 },
  { label: '1 час', value: '1h', ms: 3600e3 },
  { label: '6 часов', value: '6h', ms: 6 * 3600e3 },
  { label: '24 часа', value: '24h', ms: 24 * 3600e3 },
  { label: '7 дней', value: '7d', ms: 7 * 24 * 3600e3 },
  { label: 'Период', value: 'custom', ms: 0 },
]
const timeWindow = ref('1h')
const range = ref<[Date, Date] | null>(null)
const services = ref<string[]>([])
const levels = ref<string[]>([])
const q = ref('')
const requestId = ref('')
const live = ref(false)
const items = ref<LogEntry[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 100
const loading = ref(false)
const serviceOptions = ref<string[]>([])
const levelOptions = ref<string[]>(['DEBUG', 'INFO', 'WARNING', 'WARN', 'ERROR', 'CRITICAL'])
let timer: number | undefined

const tsFormat = new Intl.DateTimeFormat('ru-RU', {
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3,
} as Intl.DateTimeFormatOptions)
const ts = (v: string) => tsFormat.format(new Date(v))

function levelType(level: string) {
  const l = level.toUpperCase()
  if (l.startsWith('ERR') || l === 'CRITICAL' || l === 'FATAL') return 'danger'
  if (l.startsWith('WARN')) return 'warning'
  if (l === 'DEBUG') return 'info'
  return 'success'
}

function period(): { date_from?: string; date_to?: string } {
  if (timeWindow.value === 'custom') return { date_from: range.value?.[0]?.toISOString(), date_to: range.value?.[1]?.toISOString() }
  const w = windows.find((x) => x.value === timeWindow.value)!
  return { date_from: new Date(Date.now() - w.ms).toISOString(), date_to: new Date().toISOString() }
}

function filterBy(id: string) {
  requestId.value = id
  reload()
}

function reload() {
  page.value = 1
  load()
}

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const id = requestId.value.trim()
    const isUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-/.test(id)
    const res = await api.logs.search({
      ...period(),
      q: q.value,
      service: services.value,
      level: levels.value,
      request_id: id && !isUuid ? id : undefined,
      task_id: id && isUuid ? id : undefined,
      size: pageSize,
      offset: (page.value - 1) * pageSize,
    })
    items.value = res.items
    total.value = res.total
  } catch (e: any) {
    if (!silent) ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

watch(live, (on) => {
  clearInterval(timer)
  if (on) timer = setInterval(() => page.value === 1 && load(true), 3000) as unknown as number
})

onMounted(async () => {
  load()
  try {
    const opts = await api.logs.services()
    serviceOptions.value = opts.services
    if (opts.levels.length) levelOptions.value = opts.levels
  } catch {
    /* Elasticsearch may still be starting */
  }
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin: 0 0 8px;
}
.msg {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
}
.details {
  padding: 4px 48px 12px;
  display: grid;
  gap: 8px;
}
.ids {
  display: flex;
  gap: 20px;
  font-size: 12px;
}
.error-text {
  color: var(--el-color-danger);
}
.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
</style>
