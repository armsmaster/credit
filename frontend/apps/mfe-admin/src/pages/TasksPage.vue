<template>
  <div>
    <div class="summary">
      <el-card shadow="never" class="stat"><div class="label">Активные сейчас</div><div class="value">{{ summary?.active ?? '—' }}</div></el-card>
      <el-card v-for="s in statuses" :key="s" shadow="never" class="stat">
        <div class="label">{{ STATUS_META[s].label }} · 24ч</div>
        <div class="value" :class="s">{{ summary?.last_24h?.[s] ?? 0 }}</div>
      </el-card>
    </div>

    <el-tabs v-model="tab" @tab-change="reload">
      <el-tab-pane label="Активные" name="active" />
      <el-tab-pane label="История" name="history" />
    </el-tabs>

    <div class="page-toolbar">
      <el-select v-if="tab === 'history'" v-model="filters.status" multiple collapse-tags placeholder="Статус" clearable style="width: 200px" @change="reload">
        <el-option v-for="s in statuses" :key="s" :value="s" :label="STATUS_META[s].label" />
      </el-select>
      <el-select v-model="filters.task_type" placeholder="Тип задачи" clearable style="width: 280px" @change="reload">
        <el-option v-for="t in types" :key="t.task_type" :value="t.task_type" :label="t.title" />
      </el-select>
      <el-select v-model="filters.trigger" placeholder="Запуск" clearable style="width: 150px" @change="reload">
        <el-option v-for="(label, key) in TRIGGER_LABELS" :key="key" :value="key" :label="label" />
      </el-select>
      <el-date-picker v-if="tab === 'history'" v-model="range" type="datetimerange" start-placeholder="С" end-placeholder="По" style="width: 360px" @change="reload" />
      <div class="spacer" />
      <el-switch v-model="autoRefresh" active-text="Автообновление" />
      <el-button @click="reload"><el-icon><Refresh /></el-icon></el-button>
      <el-button v-if="canRun" type="primary" @click="openRun"><el-icon><VideoPlay /></el-icon>&nbsp;Запустить задачу</el-button>
    </div>

    <el-table v-loading="loading" :data="items" stripe highlight-current-row @row-click="openDetails">
      <el-table-column label="Создана" width="170">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="Задача" min-width="260">
        <template #default="{ row }">
          <div>{{ typeTitle(row.task_type) }}</div>
          <div class="mono muted">{{ row.task_type }}</div>
        </template>
      </el-table-column>
      <el-table-column label="Статус" width="130">
        <template #default="{ row }"><el-tag :type="STATUS_META[row.status as TaskStatus].type">{{ STATUS_META[row.status as TaskStatus].label }}</el-tag></template>
      </el-table-column>
      <el-table-column label="Запуск" width="200">
        <template #default="{ row }">{{ TRIGGER_LABELS[row.trigger] }} · {{ row.created_by }}</template>
      </el-table-column>
      <el-table-column label="Длительность" width="120">
        <template #default="{ row }">{{ formatDuration(row.duration_s) }}</template>
      </el-table-column>
      <el-table-column label="Ошибка" min-width="240" show-overflow-tooltip>
        <template #default="{ row }"><span class="error-text">{{ row.error }}</span></template>
      </el-table-column>
    </el-table>
    <el-pagination
      v-model:current-page="page"
      class="pager"
      layout="total, prev, pager, next"
      :page-size="pageSize"
      :total="total"
      @current-change="load"
    />

    <!-- details -->
    <el-drawer v-model="detailsOpen" :title="details ? typeTitle(details.task_type) : ''" size="50%">
      <template v-if="details">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="ID"><span class="mono">{{ details.id }}</span></el-descriptions-item>
          <el-descriptions-item label="Статус">
            <el-tag :type="STATUS_META[details.status].type">{{ STATUS_META[details.status].label }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="Тип"><span class="mono">{{ details.task_type }}</span></el-descriptions-item>
          <el-descriptions-item label="Очередь">{{ details.queue }}</el-descriptions-item>
          <el-descriptions-item label="Запуск">{{ TRIGGER_LABELS[details.trigger] }} · {{ details.created_by }}</el-descriptions-item>
          <el-descriptions-item label="Воркер">{{ details.worker || '—' }}</el-descriptions-item>
          <el-descriptions-item label="Создана">{{ formatDateTime(details.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="Начата">{{ formatDateTime(details.started_at) }}</el-descriptions-item>
          <el-descriptions-item label="Завершена">{{ formatDateTime(details.finished_at) }}</el-descriptions-item>
          <el-descriptions-item label="Длительность">{{ formatDuration(details.duration_s) }}</el-descriptions-item>
        </el-descriptions>
        <h4>Параметры</h4>
        <JsonBlock :value="details.params" />
        <template v-if="details.result">
          <h4>Результат</h4>
          <JsonBlock :value="details.result" />
        </template>
        <template v-if="details.error">
          <h4>Ошибка</h4>
          <pre class="json error-text">{{ details.error }}</pre>
        </template>
        <div class="drawer-actions">
          <el-button v-if="canRun && details.status === 'queued'" type="warning" @click="cancel(details)">Отменить</el-button>
          <el-button v-if="canRun" @click="rerun(details)">Запустить повторно</el-button>
        </div>
      </template>
    </el-drawer>

    <!-- run -->
    <el-dialog v-model="runOpen" title="Запуск задачи" width="600px">
      <el-form label-position="top">
        <el-form-item label="Тип задачи">
          <el-select v-model="runType" style="width: 100%" @change="onRunTypeChange">
            <el-option v-for="t in types" :key="t.task_type" :value="t.task_type" :label="t.title" />
          </el-select>
        </el-form-item>
        <p v-if="selectedType" class="muted">{{ selectedType.description }}</p>
        <el-form-item label="Параметры (JSON)">
          <el-input v-model="runParams" type="textarea" :rows="6" class="mono" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="runOpen = false">Отмена</el-button>
        <el-button type="primary" :disabled="!runType" @click="run">Запустить</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { formatDateTime, formatDuration, getPlatform, Role } from '@cds/platform'

import { api, STATUS_META, TRIGGER_LABELS, type TaskRun, type TaskStatus, type TaskSummary, type TaskType } from '../api'
import JsonBlock from '../components/JsonBlock.vue'

const statuses: TaskStatus[] = ['queued', 'running', 'succeeded', 'failed', 'cancelled']
const canRun = getPlatform().hasRole(Role.Operator)
const route = useRoute()

const tab = ref<'active' | 'history'>('history')
const items = ref<TaskRun[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const loading = ref(false)
const summary = ref<TaskSummary | null>(null)
const types = ref<TaskType[]>([])
const range = ref<[Date, Date] | null>(null)
const filters = reactive({ status: [] as string[], task_type: '', trigger: '' })
const autoRefresh = ref(true)
let timer: number | undefined

const detailsOpen = ref(false)
const details = ref<TaskRun | null>(null)
const runOpen = ref(false)
const runType = ref('')
const runParams = ref('{}')
const selectedType = computed(() => types.value.find((t) => t.task_type === runType.value))

function typeTitle(t: string) {
  return types.value.find((x) => x.task_type === t)?.title ?? t
}

function reload() {
  page.value = 1
  load()
}

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const status = tab.value === 'active' ? ['queued', 'running'] : filters.status
    const [res, sum] = await Promise.all([
      api.tasks.list({
        status,
        task_type: filters.task_type,
        trigger: filters.trigger,
        date_from: tab.value === 'history' ? range.value?.[0]?.toISOString() : undefined,
        date_to: tab.value === 'history' ? range.value?.[1]?.toISOString() : undefined,
        limit: pageSize,
        offset: (page.value - 1) * pageSize,
      }),
      api.tasks.summary(),
    ])
    items.value = res.items
    total.value = res.total
    summary.value = sum
    if (detailsOpen.value && details.value && ['queued', 'running'].includes(details.value.status)) {
      details.value = await api.tasks.get(details.value.id)
    }
  } catch (e: any) {
    if (!silent) ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

async function openDetails(row: TaskRun) {
  details.value = await api.tasks.get(row.id)
  detailsOpen.value = true
}

function openRun() {
  runType.value = ''
  runParams.value = '{}'
  runOpen.value = true
}

function onRunTypeChange() {
  runParams.value = JSON.stringify(selectedType.value?.default_params ?? {}, null, 2)
}

async function run() {
  let params: Record<string, unknown>
  try {
    params = JSON.parse(runParams.value || '{}')
  } catch {
    ElMessage.error('Параметры должны быть корректным JSON')
    return
  }
  try {
    const created = await api.tasks.run(runType.value, params)
    runOpen.value = false
    ElMessage.success('Задача поставлена в очередь')
    tab.value = 'active'
    await load()
    openDetails(created)
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

async function rerun(t: TaskRun) {
  try {
    const created = await api.tasks.run(t.task_type, t.params)
    ElMessage.success('Задача поставлена в очередь')
    await load()
    openDetails(created)
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

async function cancel(t: TaskRun) {
  try {
    details.value = await api.tasks.cancel(t.id)
    await load()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

watch(autoRefresh, (on) => {
  clearInterval(timer)
  if (on) timer = window.setInterval(() => load(true), 5000)
}, { immediate: true })

onMounted(async () => {
  types.value = await api.tasks.types().catch(() => [])
  await load()
  const id = route.query.task as string | undefined
  if (id) openDetails({ id } as TaskRun)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.summary {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
  margin-bottom: 8px;
}
.stat .label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.stat .value {
  font-size: 24px;
  font-weight: 600;
}
.value.failed {
  color: var(--el-color-danger);
}
.value.succeeded {
  color: var(--el-color-success);
}
.value.running {
  color: var(--el-color-primary);
}
.muted {
  color: var(--el-text-color-secondary);
}
.error-text {
  color: var(--el-color-danger);
}
.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
.drawer-actions {
  margin-top: 16px;
  display: flex;
  gap: 8px;
}
</style>
