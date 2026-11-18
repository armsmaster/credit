<template>
  <div v-if="defn" class="workspace">
    <div class="head">
      <div>
        <h2>{{ defn.code }} <small>{{ defn.name }}</small></h2>
        <div class="muted">
          {{ defn.currency_code }} · {{ defn.index_name || '—' }} · {{ defn.day_count }} · фиксированная нога: до
          {{ defn.single_payment_max_months }}M одна выплата, далее каждые {{ defn.fixed_frequency_months }}M
        </div>
      </div>
      <div class="spacer" />
      <router-link v-if="isOperator" :to="`/curves/${encodeURIComponent(defn.code)}/settings`">
        <el-button><el-icon><Setting /></el-icon>&nbsp;Параметры кривой</el-button>
      </router-link>
    </div>

    <el-tabs v-model="tab" @tab-change="onTabChange">
      <el-tab-pane label="Текущая кривая" name="current" :disabled="editing" />
      <el-tab-pane label="История" name="history" :disabled="editing" />
    </el-tabs>

    <!-- context bar (view mode) -->
    <div v-if="!editing" class="context">
      <template v-if="tab === 'current'">
        <template v-if="state">
          <span>Кривая на <b>{{ formatDate(state.as_of_date) }}</b></span>
          <span class="muted">сохранил {{ state.author }}, {{ formatDateTime(state.saved_at) }}</span>
          <span v-if="state.comment" class="muted">«{{ state.comment }}»</span>
        </template>
        <span v-else class="muted">Текущая кривая ещё не построена.</span>
        <div class="spacer" />
        <el-button v-if="isOperator" type="primary" @click="startEdit('state')">
          <el-icon><EditPen /></el-icon>&nbsp;{{ state ? 'Построить / обновить' : 'Построить кривую' }}
        </el-button>
        <el-button v-if="isOperator && state" @click="archiveNow">Перенести в историю</el-button>
      </template>
      <template v-else>
        <template v-if="historySnap">
          <span>Историческая кривая на <b>{{ formatDate(historySnap.as_of_date) }}</b></span>
          <el-select v-model="selectedVersion" size="small" style="width: 300px" @change="loadVersion">
            <el-option
              v-for="v in versions"
              :key="v.version"
              :value="v.version"
              :label="`Версия ${v.version} · ${SOURCE_LABELS[v.source] || v.source} · ${v.author}`"
            />
          </el-select>
          <el-tag v-if="historySnap.source === 'manual_edit'" type="warning" size="small">изменение задним числом</el-tag>
          <span v-if="historySnap.comment" class="muted ellipsis">«{{ historySnap.comment }}»</span>
        </template>
        <span v-else class="muted">Выберите дату в списке слева.</span>
        <div class="spacer" />
        <el-button v-if="isOperator && historySnap" type="primary" @click="startEdit('history', historySnap.as_of_date)">
          <el-icon><EditPen /></el-icon>&nbsp;Редактировать (новая версия)
        </el-button>
      </template>
    </div>

    <div class="body" :class="layoutClass">
      <!-- history dates -->
      <aside v-if="tab === 'history' && !editing" class="side">
        <el-card shadow="never" body-style="padding: 8px">
          <el-date-picker
            v-model="historyRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="С"
            end-placeholder="По"
            size="small"
            style="width: 100%"
            @change="loadHistory"
          />
          <el-table :data="history" size="small" height="460" highlight-current-row class="dates" @row-click="(r: HistoryDate) => selectDate(r.as_of_date)">
            <el-table-column label="Дата" width="105">
              <template #default="{ row }"><span :class="{ sel: row.as_of_date === selectedDate }">{{ formatDate(row.as_of_date) }}</span></template>
            </el-table-column>
            <el-table-column label="Верс." width="60" align="center">
              <template #default="{ row }">{{ row.latest_version }}</template>
            </el-table-column>
            <el-table-column label="">
              <template #default="{ row }">
                <el-tooltip v-if="row.edited" content="Есть изменения задним числом"><el-icon color="var(--el-color-warning)"><Clock /></el-icon></el-tooltip>
              </template>
            </el-table-column>
          </el-table>
          <div v-if="isOperator" class="new-date">
            <el-date-picker v-model="newHistoryDate" type="date" value-format="YYYY-MM-DD" size="small" placeholder="Дата" :disabled-date="isFuture" />
            <el-button size="small" :disabled="!newHistoryDate" @click="startEdit('history', newHistoryDate!)">Ввести кривую</el-button>
          </div>
        </el-card>
      </aside>

      <!-- calculator inputs -->
      <aside v-if="editing" class="side editor">
        <el-card shadow="never">
          <template #header>
            <b>{{ editKind === 'state' ? 'Построение текущей кривой' : `Изменение истории на ${formatDate(editDate)}` }}</b>
          </template>
          <el-alert
            v-if="editKind === 'history'"
            type="warning"
            :closable="false"
            show-icon
            title="Изменение задним числом"
            description="Сохранится новая версия, запись в журнале получит тег «изменение задним числом»."
            class="mb"
          />
          <el-form label-position="top" size="small">
            <el-form-item label="Дата кривой">
              <el-date-picker v-model="editDate" type="date" value-format="YYYY-MM-DD" :disabled="editKind === 'history'" :disabled-date="isFuture" />
            </el-form-item>
          </el-form>
          <QuotesEditor v-model:mode="inputMode" v-model:quotes="quotes" @valid="(v: boolean) => (quotesValid = v)" />
          <el-divider />
          <ParamsForm v-model="params" :defaults="defn.effective_params" />
          <div class="calc-row">
            <el-button type="primary" plain :loading="calculating" :disabled="!quotesValid" @click="recalc">Рассчитать</el-button>
            <el-switch v-model="autoCalc" active-text="авто" />
          </div>
          <el-alert v-if="calcError" type="error" :title="calcError" :closable="false" show-icon class="mb" />
          <el-input
            v-model="comment"
            type="textarea"
            :rows="2"
            :placeholder="editKind === 'history' ? 'Причина изменения (обязательно)' : 'Комментарий (необязательно)'"
          />
          <div class="save-row">
            <el-button @click="cancelEdit">Отмена</el-button>
            <el-button type="primary" :loading="saving" :disabled="!canSave" @click="save">
              {{ editKind === 'history' ? 'Сохранить новую версию' : 'Сохранить' }}
            </el-button>
          </div>
        </el-card>
      </aside>

      <!-- charts and results -->
      <section class="main">
        <div class="view-toolbar">
          <el-select v-model="format" style="width: 250px">
            <el-option v-for="(label, key) in FORMAT_LABELS" :key="key" :value="key" :label="label" />
          </el-select>
          <span class="muted">Сравнить с:</span>
          <el-checkbox-group v-model="compareOn">
            <el-checkbox v-for="c in COMPARE_LABELS" :key="c.key" :value="c.key" :disabled="!compareByLabel[c.key]">
              {{ c.title }}
              <small v-if="compareByLabel[c.key]" class="muted">({{ formatDate(compareByLabel[c.key]!.as_of_date) }})</small>
            </el-checkbox>
          </el-checkbox-group>
        </div>

        <template v-if="displayed">
          <CurveCharts :result="displayed" :comparisons="visibleComparisons" :format="format" />
          <StatsPanel :result="displayed" class="mt" />
          <el-tabs type="border-card" class="mt">
            <el-tab-pane label="Котировки и подгонка">
              <FitTable :fitted="displayed.fitted" :params="displayed.params" />
            </el-tab-pane>
            <el-tab-pane label="Калькулятор на дату">
              <RateCalculator :as-of="displayed.as_of_date" :nodes="displayed.nodes" :day-count="displayed.conventions.day_count" :format="format" />
            </el-tab-pane>
            <el-tab-pane label="Узлы кривой">
              <NodesTable :nodes="displayed.nodes" :format="format" />
            </el-tab-pane>
          </el-tabs>
        </template>
        <el-empty v-else :description="editing ? 'Введите котировки и нажмите «Рассчитать»' : 'Нет данных'" />
      </section>
    </div>
  </div>
  <div v-else v-loading="true" style="height: 300px" />
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import { computed, h, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { formatDate, formatDateTime, getPlatform, Role } from '@cds/platform'

import { api } from '../api'
import CurveCharts from '../components/CurveCharts.vue'
import FitTable from '../components/FitTable.vue'
import NodesTable from '../components/NodesTable.vue'
import ParamsForm from '../components/ParamsForm.vue'
import QuotesEditor from '../components/QuotesEditor.vue'
import RateCalculator from '../components/RateCalculator.vue'
import StatsPanel from '../components/StatsPanel.vue'
import { FORMAT_LABELS } from '../rates'
import type {
  CalibrationResult,
  CompareCurve,
  Definition,
  HistoryDate,
  InputMode,
  Params,
  RateFormat,
  Snapshot,
  Version,
} from '../types'

type Row = Record<string, unknown>
type CompareKey = 'day' | 'week' | 'month'

const COMPARE_LABELS: { key: CompareKey; title: string }[] = [
  { key: 'day', title: 'Вчера' },
  { key: 'week', title: 'Неделя назад' },
  { key: 'month', title: 'Месяц назад' },
]
const SOURCE_LABELS: Record<string, string> = { snapshot: 'ночной перенос', auto_archive: 'автоперенос', manual_edit: 'ручное изменение' }
const PREFS_KEY = 'curves.chart'

const route = useRoute()
const router = useRouter()
const platform = getPlatform()
const isOperator = platform.hasRole(Role.Operator)
const code = computed(() => String(route.params.code))

const defn = ref<Definition | null>(null)
const today = ref('')
const tab = ref<'current' | 'history'>(route.query.tab === 'history' ? 'history' : 'current')

const state = ref<Snapshot | null>(null)
const history = ref<HistoryDate[]>([])
const historyRange = ref<[string, string] | null>(null)
const selectedDate = ref<string | null>((route.query.date as string) || null)
const versions = ref<Version[]>([])
const selectedVersion = ref<number | null>(null)
const historySnap = ref<Snapshot | null>(null)
const newHistoryDate = ref<string | null>(null)

const editing = ref(false)
const editKind = ref<'state' | 'history'>('state')
const editDate = ref('')
const inputMode = ref<InputMode>('bid_ask')
const quotes = ref<Row[]>([])
const quotesValid = ref(false)
const params = ref<Params>({} as Params)
const comment = ref('')
const calc = ref<CalibrationResult | null>(null)
const calcError = ref('')
const calculating = ref(false)
const autoCalc = ref(true)
const saving = ref(false)

const format = ref<RateFormat>('continuous')
const compareOn = ref<CompareKey[]>([])
const comparisons = ref<CompareCurve[]>([])

const viewed = computed(() => (tab.value === 'current' ? state.value : historySnap.value))
const displayed = computed<CalibrationResult | null>(() => (editing.value ? calc.value : (viewed.value?.result ?? null)))
const displayedAsOf = computed(() => (editing.value ? editDate.value : (viewed.value?.as_of_date ?? '')))
const compareByLabel = computed(() => Object.fromEntries(comparisons.value.map((c) => [c.label, c])) as Partial<Record<CompareKey, CompareCurve>>)
const visibleComparisons = computed(() => comparisons.value.filter((c) => compareOn.value.includes(c.label)))
const layoutClass = computed(() => (editing.value ? 'with-editor' : tab.value === 'history' ? 'with-dates' : ''))
const canSave = computed(
  () => !!calc.value && quotesValid.value && !calculating.value && (editKind.value === 'state' || comment.value.trim().length >= 3),
)

const isFuture = (d: Date) => !!today.value && d.getTime() > new Date(today.value + 'T00:00:00').getTime()

// ------------------------------------------------------------------ loading
async function loadDefinition() {
  defn.value = await api.definitions.get(code.value)
}

async function loadState() {
  try {
    state.value = await api.state.get(code.value)
  } catch (e: any) {
    if (e.status !== 404) ElMessage.error(e.message)
    state.value = null
  }
}

async function loadHistory() {
  history.value = await api.history.list(code.value, historyRange.value?.[0], historyRange.value?.[1])
  if (!selectedDate.value && history.value.length) await selectDate(history.value[0].as_of_date)
  else if (selectedDate.value && !historySnap.value) await selectDate(selectedDate.value)
}

async function selectDate(date: string) {
  selectedDate.value = date
  try {
    versions.value = await api.history.versions(code.value, date)
    selectedVersion.value = versions.value[0]?.version ?? null
    await loadVersion()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
  syncQuery()
}

async function loadVersion() {
  if (!selectedDate.value) return
  historySnap.value = await api.history.get(code.value, selectedDate.value, selectedVersion.value)
}

async function loadComparisons() {
  if (!displayedAsOf.value) {
    comparisons.value = []
    return
  }
  try {
    comparisons.value = await api.compare(code.value, displayedAsOf.value)
  } catch {
    comparisons.value = []
  }
}

function syncQuery() {
  router.replace({ query: { tab: tab.value, date: tab.value === 'history' ? selectedDate.value ?? undefined : undefined } })
}

async function onTabChange() {
  syncQuery()
  if (tab.value === 'history') await loadHistory()
}

// ------------------------------------------------------------------ calculator
function startEdit(kind: 'state' | 'history', date?: string) {
  const source = kind === 'state' ? state.value : historySnap.value ?? state.value
  editKind.value = kind
  editDate.value = kind === 'state' ? today.value : date!
  inputMode.value = source?.input_mode ?? 'bid_ask'
  quotes.value = source ? JSON.parse(JSON.stringify(source.quotes)) : []
  quotesValid.value = quotes.value.length > 0
  params.value = { ...defn.value!.effective_params, ...(source?.params ?? {}) }
  comment.value = ''
  calc.value = null
  calcError.value = ''
  editing.value = true
  if (quotesValid.value) recalc()
}

function cancelEdit() {
  editing.value = false
  calc.value = null
}

let calcSeq = 0
async function recalc() {
  if (!quotesValid.value || !editDate.value) return
  const my = ++calcSeq
  calculating.value = true
  try {
    const res = await api.calibrate(code.value, editDate.value, { input_mode: inputMode.value, quotes: quotes.value, params: params.value })
    if (my === calcSeq) {
      calc.value = res
      calcError.value = ''
    }
  } catch (e: any) {
    if (my === calcSeq) calcError.value = e.message
  } finally {
    if (my === calcSeq) calculating.value = false
  }
}

let autoTimer: number | undefined
watch(
  [quotes, inputMode, params, editDate],
  () => {
    if (!editing.value || !autoCalc.value) return
    clearTimeout(autoTimer)
    autoTimer = window.setTimeout(recalc, 450)
  },
  { deep: true },
)

async function save() {
  saving.value = true
  const payload = { input_mode: inputMode.value, quotes: quotes.value, params: params.value }
  try {
    if (editKind.value === 'state') {
      state.value = await api.state.save(code.value, editDate.value, comment.value, payload)
      ElMessage.success(`Текущая кривая на ${formatDate(editDate.value)} сохранена`)
    } else {
      const snap = await api.history.save(code.value, editDate.value, comment.value.trim(), payload)
      ElMessage.success(`Сохранена версия ${snap.version} (изменение задним числом)`)
      await loadHistory()
      await selectDate(editDate.value)
    }
    editing.value = false
    await loadDefinition()
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

async function archiveNow() {
  try {
    await ElMessageBox.confirm(
      `Перенести текущую кривую на ${formatDate(state.value!.as_of_date)} в историю сейчас? После переноса изменить её можно будет только как изменение задним числом.`,
      'Перенос в историю',
      { type: 'warning' },
    )
    const { task_id } = await api.state.archive(code.value)
    ElNotification({
      title: 'Задача поставлена в очередь',
      type: 'success',
      message: h('a', { href: '#', onClick: (ev: Event) => { ev.preventDefault(); router.push({ path: '/admin/tasks', query: { task: task_id } }) } }, 'Открыть задачу'),
    })
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error(e.message)
  }
}

// ------------------------------------------------------------------ preferences (per user)
let prefsLoaded = false
let prefsTimer: number | undefined
watch([format, compareOn], () => {
  if (!prefsLoaded) return
  clearTimeout(prefsTimer)
  prefsTimer = window.setTimeout(() => platform.preferences.set(PREFS_KEY, { format: format.value, compare: compareOn.value }), 400)
})

watch(displayedAsOf, loadComparisons)

onMounted(async () => {
  try {
    const [meta, prefs] = await Promise.all([api.meta(), platform.preferences.get<{ format?: RateFormat; compare?: CompareKey[] }>(PREFS_KEY)])
    today.value = meta.today
    if (prefs?.format) format.value = prefs.format
    if (prefs?.compare) compareOn.value = prefs.compare
    prefsLoaded = true
    await loadDefinition()
    await loadState()
    if (tab.value === 'history') await loadHistory()
    await loadComparisons()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
})
</script>

<style scoped>
.head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.head h2 {
  margin: 0 0 4px;
}
.head h2 small {
  font-weight: 400;
  color: var(--el-text-color-secondary);
  font-size: 15px;
}
.spacer {
  flex: 1;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.ellipsis {
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.context {
  display: flex;
  align-items: center;
  gap: 12px;
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 12px;
  min-height: 34px;
}
.body {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}
.body.with-editor {
  grid-template-columns: 440px minmax(0, 1fr);
}
.body.with-dates {
  grid-template-columns: 270px minmax(0, 1fr);
}
.side.editor {
  position: sticky;
  top: 0;
}
.view-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.calc-row,
.save-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 12px 0;
}
.save-row {
  justify-content: flex-end;
}
.mb {
  margin-bottom: 12px;
}
.mt {
  margin-top: 12px;
}
.dates {
  margin-top: 8px;
  cursor: pointer;
}
.sel {
  font-weight: 700;
  color: var(--el-color-primary);
}
.new-date {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}
</style>
