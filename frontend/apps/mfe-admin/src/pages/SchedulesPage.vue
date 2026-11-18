<template>
  <div>
    <div class="page-toolbar">
      <span class="muted">Время срабатывания считается во временной зоне расписания. Формат cron: минуты часы день месяц день_недели.</span>
      <div class="spacer" />
      <el-button @click="load"><el-icon><Refresh /></el-icon></el-button>
      <el-button type="primary" @click="openEdit()"><el-icon><Plus /></el-icon>&nbsp;Новое расписание</el-button>
    </div>

    <el-table v-loading="loading" :data="items" stripe>
      <el-table-column label="Название" min-width="220">
        <template #default="{ row }">
          <div>{{ row.name }}</div>
          <div class="mono muted">{{ row.task_type }}</div>
        </template>
      </el-table-column>
      <el-table-column label="Cron" width="150">
        <template #default="{ row }"><span class="mono">{{ row.cron }}</span></template>
      </el-table-column>
      <el-table-column prop="timezone" label="Зона" width="140" />
      <el-table-column label="Вкл." width="80">
        <template #default="{ row }"><el-switch :model-value="row.enabled" @change="(v: boolean) => toggle(row, v)" /></template>
      </el-table-column>
      <el-table-column label="Следующий запуск" width="170">
        <template #default="{ row }">{{ row.enabled ? formatDateTime(row.next_run_at) : '—' }}</template>
      </el-table-column>
      <el-table-column label="Последний запуск" width="230">
        <template #default="{ row }">
          <span>{{ formatDateTime(row.last_run_at) }}</span>
          <el-tag v-if="row.last_run_status" size="small" :type="STATUS_META[row.last_run_status as TaskStatus].type" class="ml">
            {{ STATUS_META[row.last_run_status as TaskStatus].label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="Изменено" width="180">
        <template #default="{ row }">
          <div>{{ row.updated_by }}</div>
          <div class="muted">{{ formatDateTime(row.updated_at) }}</div>
        </template>
      </el-table-column>
      <el-table-column width="260" align="right">
        <template #default="{ row }">
          <el-button size="small" @click="runNow(row)">Запустить сейчас</el-button>
          <el-button size="small" @click="openEdit(row)">Изменить</el-button>
          <el-button size="small" type="danger" plain @click="remove(row)"><el-icon><Delete /></el-icon></el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="editOpen" :title="editing ? 'Изменение расписания' : 'Новое расписание'" width="620px">
      <el-form label-width="150px">
        <el-form-item label="Задача">
          <el-select v-model="form.task_type" style="width: 100%" @change="onTypeChange">
            <el-option v-for="t in types" :key="t.task_type" :value="t.task_type" :label="`${t.title} (${t.task_type})`" />
          </el-select>
        </el-form-item>
        <el-form-item label="Название"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="Cron">
          <el-input v-model="form.cron" class="mono" placeholder="30 0 * * *" @input="schedulePreview" />
          <div class="presets">
            <el-link v-for="p in presets" :key="p.cron" type="primary" @click="form.cron = p.cron; schedulePreview()">{{ p.label }}</el-link>
          </div>
        </el-form-item>
        <el-form-item label="Временная зона"><el-input v-model="form.timezone" @input="schedulePreview" /></el-form-item>
        <el-form-item label="Ближайшие запуски">
          <div v-if="previewError" class="error-text">{{ previewError }}</div>
          <div v-else class="preview">
            <div v-for="r in preview" :key="r">{{ formatDateTime(r) }}</div>
          </div>
        </el-form-item>
        <el-form-item label="Параметры (JSON)"><el-input v-model="paramsText" type="textarea" :rows="4" class="mono" /></el-form-item>
        <el-form-item label="Включено"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editOpen = false">Отмена</el-button>
        <el-button type="primary" :loading="saving" @click="save">Сохранить</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { formatDateTime } from '@cds/platform'

import { api, STATUS_META, type Schedule, type ScheduleIn, type TaskStatus, type TaskType } from '../api'

const router = useRouter()
const items = ref<Schedule[]>([])
const types = ref<TaskType[]>([])
const loading = ref(false)
const editOpen = ref(false)
const saving = ref(false)
const editing = ref<Schedule | null>(null)
const form = reactive<ScheduleIn>({ task_type: '', name: '', cron: '30 0 * * *', timezone: 'Europe/Moscow', params: {}, enabled: true })
const paramsText = ref('{}')
const preview = ref<string[]>([])
const previewError = ref('')
let previewTimer: number | undefined

const presets = [
  { label: 'каждую ночь 00:30', cron: '30 0 * * *' },
  { label: 'будни 19:00', cron: '0 19 * * 1-5' },
  { label: 'каждый час', cron: '0 * * * *' },
  { label: 'каждые 15 минут', cron: '*/15 * * * *' },
]

async function load() {
  loading.value = true
  try {
    items.value = await api.schedules.list()
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function openEdit(s?: Schedule) {
  editing.value = s ?? null
  Object.assign(form, s
    ? { task_type: s.task_type, name: s.name, cron: s.cron, timezone: s.timezone, params: s.params, enabled: s.enabled }
    : { task_type: '', name: '', cron: '30 0 * * *', timezone: 'Europe/Moscow', params: {}, enabled: true })
  paramsText.value = JSON.stringify(form.params, null, 2)
  editOpen.value = true
  schedulePreview()
}

function onTypeChange() {
  const t = types.value.find((x) => x.task_type === form.task_type)
  if (t && !form.name) form.name = t.title
  if (t && paramsText.value.trim() === '{}') paramsText.value = JSON.stringify(t.default_params, null, 2)
}

function schedulePreview() {
  clearTimeout(previewTimer)
  previewTimer = window.setTimeout(async () => {
    try {
      preview.value = (await api.schedules.preview(form.cron, form.timezone)).next_runs
      previewError.value = ''
    } catch (e: any) {
      previewError.value = e.message
      preview.value = []
    }
  }, 300)
}

async function save() {
  try {
    form.params = JSON.parse(paramsText.value || '{}')
  } catch {
    ElMessage.error('Параметры должны быть корректным JSON')
    return
  }
  saving.value = true
  try {
    if (editing.value) await api.schedules.update(editing.value.id, { ...form })
    else await api.schedules.create({ ...form })
    editOpen.value = false
    ElMessage.success('Расписание сохранено')
    await load()
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

async function toggle(s: Schedule, enabled: boolean) {
  try {
    await api.schedules.update(s.id, { task_type: s.task_type, name: s.name, cron: s.cron, timezone: s.timezone, params: s.params, enabled })
    await load()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

async function runNow(s: Schedule) {
  try {
    const run = await api.schedules.run(s.id)
    ElMessage.success('Задача поставлена в очередь')
    router.push({ path: '/admin/tasks', query: { task: run.id } })
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

async function remove(s: Schedule) {
  try {
    await ElMessageBox.confirm(`Удалить расписание «${s.name}»?`, 'Удаление', { type: 'warning' })
    await api.schedules.remove(s.id)
    await load()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error(e.message)
  }
}

onMounted(async () => {
  types.value = await api.tasks.types().catch(() => [])
  await load()
})
</script>

<style scoped>
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.ml {
  margin-left: 6px;
}
.presets {
  display: flex;
  gap: 12px;
  margin-top: 4px;
}
.preview {
  font-size: 13px;
  line-height: 1.6;
}
.error-text {
  color: var(--el-color-danger);
}
</style>
