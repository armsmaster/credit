<template>
  <div v-if="defn" class="workspace">
    <div class="head">
      <div>
        <h2>{{ defn.code }} <small>{{ defn.name }}</small></h2>
        <div class="muted">
          {{ defn.issuer_title }} · recovery {{ Math.round(defn.effective_params.recovery_rate * 100) }}% · дисконтирование {{ defn.discount_curve_code }}
          · {{ defn.bonds.length }} бумаг в составе
        </div>
      </div>
      <div class="spacer" />
      <router-link v-if="isOperator" :to="`/credit/${encodeURIComponent(defn.code)}/settings`">
        <el-button><el-icon><Setting /></el-icon>&nbsp;Состав и параметры</el-button>
      </router-link>
    </div>

    <el-tabs v-model="tab" @tab-change="onTabChange">
      <el-tab-pane label="Текущая кривая" name="current" :disabled="editing" />
      <el-tab-pane label="История" name="history" :disabled="editing" />
    </el-tabs>

    <div v-if="!editing" class="context">
      <template v-if="tab === 'current'">
        <template v-if="state">
          <span>Кривая на <b>{{ formatDate(state.as_of_date) }}</b></span>
          <span class="muted">сохранил {{ state.author }}, {{ formatDateTime(state.saved_at) }}</span>
          <span v-if="state.comment" class="muted ellipsis">«{{ state.comment }}»</span>
        </template>
        <span v-else class="muted">Текущая кривая ещё не построена.</span>
        <div class="spacer" />
        <el-button v-if="isOperator" type="primary" @click="startEdit('state')">
          <el-icon><EditPen /></el-icon>&nbsp;Онлайн-расчёт
        </el-button>
        <el-button v-if="isOperator && state" @click="archiveNow">Перенести в историю</el-button>
        <el-tooltip v-if="state" :content="EXCEL_HINT">
          <el-button :loading="exporting" @click="exportExcel"><el-icon><Download /></el-icon>&nbsp;Excel</el-button>
        </el-tooltip>
      </template>
      <template v-else>
        <template v-if="historySnap">
          <span>Историческая кривая на <b>{{ formatDate(historySnap.as_of_date) }}</b></span>
          <el-select v-model="selectedVersion" size="small" style="width: 300px" @change="loadVersion">
            <el-option v-for="v in versions" :key="v.version" :value="v.version" :label="`Версия ${v.version} · ${SOURCE_LABELS[v.source] || v.source} · ${v.author}`" />
          </el-select>
          <el-tag v-if="historySnap.source === 'manual_edit'" type="warning" size="small">изменение задним числом</el-tag>
          <span v-if="historySnap.comment" class="muted ellipsis">«{{ historySnap.comment }}»</span>
        </template>
        <span v-else class="muted">Выберите дату в списке слева.</span>
        <div class="spacer" />
        <el-button v-if="isOperator && historySnap" type="primary" @click="startEdit('history', historySnap.as_of_date)">
          <el-icon><EditPen /></el-icon>&nbsp;Редактировать (новая версия)
        </el-button>
        <el-tooltip v-if="historySnap" :content="EXCEL_HINT">
          <el-button :loading="exporting" @click="exportExcel"><el-icon><Download /></el-icon>&nbsp;Excel</el-button>
        </el-tooltip>
      </template>
    </div>

    <div class="body" :class="layoutClass">
      <aside v-if="tab === 'history' && !editing" class="side">
        <el-card shadow="never" body-style="padding: 8px">
          <el-date-picker v-model="historyRange" type="daterange" value-format="YYYY-MM-DD" start-placeholder="С" end-placeholder="По" size="small" style="width: 100%" @change="loadHistory" />
          <el-table :data="history" size="small" height="460" highlight-current-row class="dates" @row-click="(r: HistoryDate) => selectDate(r.as_of_date)">
            <el-table-column label="Дата" width="105">
              <template #default="{ row }"><span :class="{ sel: row.as_of_date === selectedDate }">{{ formatDate(row.as_of_date) }}</span></template>
            </el-table-column>
            <el-table-column label="Верс." width="60" align="center"><template #default="{ row }">{{ row.latest_version }}</template></el-table-column>
            <el-table-column label="">
              <template #default="{ row }">
                <el-tooltip v-if="row.edited" content="Есть изменения задним числом"><el-icon color="var(--el-color-warning)"><Clock /></el-icon></el-tooltip>
              </template>
            </el-table-column>
          </el-table>
          <div v-if="isOperator" class="new-date">
            <el-date-picker v-model="newHistoryDate" type="date" value-format="YYYY-MM-DD" size="small" placeholder="Дата" :disabled-date="notPast" />
            <el-button size="small" :disabled="!newHistoryDate" @click="startEdit('history', newHistoryDate!)">Построить</el-button>
          </div>
        </el-card>
      </aside>

      <aside v-if="editing" class="side editor">
        <el-card shadow="never">
          <template #header>
            <b>{{ editKind === 'state' ? 'Онлайн-расчёт текущей кривой' : `Изменение истории на ${formatDate(editDate)}` }}</b>
          </template>
          <el-alert v-if="editKind === 'history'" type="warning" :closable="false" show-icon class="mb" title="Изменение задним числом"
            description="Расчёт по ценам на 19:00 этой даты. Сохранится новая версия с тегом «изменение задним числом»." />
          <div class="row">
            <span class="muted">Дата кривой: <b>{{ formatDate(editDate) }}</b></span>
            <div class="spacer" />
            <el-button v-if="editKind === 'state'" size="small" :loading="refreshing" @click="refreshMarket">
              <el-icon><Refresh /></el-icon>&nbsp;Котировки с биржи
            </el-button>
          </div>
          <p class="muted small">
            Цены обновляются каждые 15 минут (данные ISS с задержкой 15 мин). В таблице бумаг клик по цене исключает её из расчёта.
          </p>
          <el-divider />
          <ParamsForm v-model="params" :defaults="defn.effective_params" />
          <div class="calc-row">
            <el-button type="primary" plain :loading="calculating" @click="recalc">Рассчитать</el-button>
            <el-tooltip :content="EXCEL_HINT">
              <el-button :loading="exporting" :disabled="!calc" @click="exportExcel"><el-icon><Download /></el-icon>&nbsp;Excel</el-button>
            </el-tooltip>
            <el-switch v-model="autoCalc" active-text="авто" />
          </div>
          <el-alert v-if="calcError" type="error" :title="calcError" :closable="false" show-icon class="mb" />
          <el-input v-model="comment" type="textarea" :rows="2" :placeholder="editKind === 'history' ? 'Причина изменения (обязательно)' : 'Комментарий (необязательно)'" />
          <div class="save-row">
            <el-button @click="cancelEdit">Отмена</el-button>
            <el-button type="primary" :loading="saving" :disabled="!canSave" @click="save">
              {{ editKind === 'history' ? 'Сохранить новую версию' : 'Сохранить' }}
            </el-button>
          </div>
        </el-card>
      </aside>

      <section class="main">
        <div class="view-toolbar">
          <span class="muted">Сравнить с:</span>
          <el-checkbox-group v-model="compareOn">
            <el-checkbox v-for="c in COMPARE_LABELS" :key="c.key" :value="c.key" :disabled="!compareByLabel[c.key]">
              {{ c.title }}
              <small v-if="compareByLabel[c.key]" class="muted">({{ formatDate(compareByLabel[c.key]!.as_of_date) }})</small>
            </el-checkbox>
          </el-checkbox-group>
        </div>

        <template v-if="displayed">
          <CreditCharts v-model:view="chartView" :result="displayed" :comparisons="visibleComparisons" />
          <StatsPanel :result="displayed" :inputs="displayedInputs" class="mt" />
        </template>
        <el-empty v-else-if="!editing" description="Нет данных" />
        <el-alert v-else-if="!calculating && calcInputs?.warnings?.length" type="warning" :closable="false" show-icon title="Кривая не построена">
          <div v-for="w in calcInputs.warnings" :key="w">• {{ w }}</div>
        </el-alert>

        <el-tabs v-if="displayedRows.length" type="border-card" class="mt">
          <el-tab-pane :label="`Бумаги и цены (${displayedRows.length})`">
            <BondInputsTable :rows="displayedRows" :result="displayed" :editable="editing" @change="scheduleRecalc" @open="(s) => (openSecid = s)" />
          </el-tab-pane>
          <el-tab-pane v-if="displayed" label="Узлы кривой" lazy>
            <NodesTable :nodes="displayed.nodes" />
          </el-tab-pane>
          <el-tab-pane v-if="editing && calcRows.some((r) => r.cashflows?.length)" label="Денежные потоки" lazy>
            <el-select v-model="cashflowSecid" placeholder="Бумага" style="width: 260px" class="mb">
              <el-option v-for="r in calcRows.filter((x) => x.cashflows?.length)" :key="r.secid" :value="r.secid" :label="r.shortname" />
            </el-select>
            <el-table :data="calcRows.find((r) => r.secid === cashflowSecid)?.cashflows ?? []" size="small" max-height="400">
              <el-table-column label="Дата" width="120"><template #default="{ row }">{{ formatDate(row.date) }}</template></el-table-column>
              <el-table-column label="Тип" width="140"><template #default="{ row }">{{ CF_KIND[row.kind] ?? row.kind }}</template></el-table-column>
              <el-table-column label="Сумма, ₽" width="130" align="right"><template #default="{ row }">{{ num(row.amount, 2) }}</template></el-table-column>
              <el-table-column label="Ставка, %" width="110" align="right"><template #default="{ row }">{{ num(row.rate_pct, 3) }}</template></el-table-column>
              <el-table-column label=""><template #default="{ row }"><el-tag v-if="row.projected" size="small" type="info">прогноз по кривой</el-tag></template></el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </section>
    </div>

    <BondDrawer :secid="openSecid" :editable="isOperator" @close="openSecid = null" />
  </div>
  <div v-else v-loading="true" style="height: 300px" />
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import { computed, h, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { formatDate, formatDateTime, getPlatform, Role } from '@cds/platform'

import { api, type CalcPayload } from '../api'
import BondDrawer from '../components/BondDrawer.vue'
import BondInputsTable from '../components/BondInputsTable.vue'
import CreditCharts from '../components/CreditCharts.vue'
import NodesTable from '../components/NodesTable.vue'
import ParamsForm from '../components/ParamsForm.vue'
import StatsPanel from '../components/StatsPanel.vue'
import { num, SOURCE_LABELS } from '../format'
import type { CalcBond, CalcInputs, CalcResult, CompareCurve, Definition, HistoryDate, Params, Snapshot, Version } from '../types'

type CompareKey = 'day' | 'week' | 'month'
const COMPARE_LABELS: { key: CompareKey; title: string }[] = [
  { key: 'day', title: 'Вчера' },
  { key: 'week', title: 'Неделя назад' },
  { key: 'month', title: 'Месяц назад' },
]
const CF_KIND: Record<string, string> = { coupon: 'купон', amortization: 'амортизация', redemption: 'погашение / оферта' }
const PREFS_KEY = 'credit.chart'
const EXCEL_HINT = 'Excel с детализацией расчёта: потоки облигаций, прогноз купонов и номиналов, дожитие, recovery, дисконтирование и модельные цены — формулами'

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
const params = ref<Params>({} as Params)
const comment = ref('')
const calcRows = ref<CalcBond[]>([])
const calc = ref<CalcResult | null>(null)
const calcInputs = ref<CalcInputs | null>(null)
const calcError = ref('')
const calculating = ref(false)
const refreshing = ref(false)
const autoCalc = ref(true)
const saving = ref(false)
const cashflowSecid = ref<string | null>(null)
const openSecid = ref<string | null>(null)

const chartView = ref<'spread' | 'survival' | 'pd'>('spread')
const compareOn = ref<CompareKey[]>([])
const comparisons = ref<CompareCurve[]>([])

const viewed = computed(() => (tab.value === 'current' ? state.value : historySnap.value))
const displayed = computed<CalcResult | null>(() => (editing.value ? calc.value : (viewed.value?.result ?? null)))
const displayedInputs = computed(() => (editing.value ? calcInputs.value : (viewed.value?.inputs ?? null)))
const displayedRows = computed<CalcBond[]>(() => (editing.value ? calcRows.value : ((viewed.value?.bonds ?? []) as CalcBond[])))
const displayedAsOf = computed(() => (editing.value ? editDate.value : (viewed.value?.as_of_date ?? '')))
const compareByLabel = computed(() => Object.fromEntries(comparisons.value.map((c) => [c.label, c])) as Partial<Record<CompareKey, CompareCurve>>)
const visibleComparisons = computed(() => comparisons.value.filter((c) => compareOn.value.includes(c.label)))
const layoutClass = computed(() => (editing.value ? 'with-editor' : tab.value === 'history' ? 'with-dates' : ''))
const canSave = computed(() => !!calc.value && !calculating.value && (editKind.value === 'state' || comment.value.trim().length >= 3))
const notPast = (d: Date) => !!today.value && d.getTime() >= new Date(today.value + 'T00:00:00').getTime()

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
  if (!displayedAsOf.value) return (comparisons.value = [])
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

// ------------------------------------------------------------------ online calculation
type BondSetting = Pick<CalcBond, 'secid' | 'weight' | 'enabled' | 'manual_price' | 'excluded_obs'>

function settingsFrom(snapshot: Snapshot | null, kind: 'state' | 'history'): BondSetting[] | null {
  const saved = new Map((snapshot?.bonds ?? []).map((b) => [b.secid, b]))
  const pick = (b: Partial<CalcBond> & { secid: string }, weight = 1): BondSetting => ({
    secid: b.secid, weight: b.weight ?? weight, enabled: b.enabled ?? true, manual_price: b.manual_price ?? null, excluded_obs: b.excluded_obs ?? [],
  })
  if (kind === 'history' && snapshot) return snapshot.bonds.map((b) => pick(b))
  // current curve: today's composition, with the switches saved in the last state
  return defn.value!.bonds.map((b) => pick(saved.get(b.secid) ?? { secid: b.secid, weight: b.weight }, b.weight))
}

let initialBonds: BondSetting[] | null = null

function payload(): CalcPayload {
  // until the first calculation returns, the table is empty: keep the composition we started with
  const bonds = calcRows.value.length
    ? calcRows.value.map((r) => ({ secid: r.secid, weight: r.weight, enabled: r.enabled, manual_price: r.manual_price || null, excluded_obs: r.excluded_obs }))
    : initialBonds
  return { bonds, params: params.value }
}

async function startEdit(kind: 'state' | 'history', date?: string) {
  const source = kind === 'state' ? state.value : historySnap.value
  editKind.value = kind
  editDate.value = kind === 'state' ? today.value : date!
  params.value = { ...defn.value!.effective_params, ...(source?.params ?? {}) }
  comment.value = ''
  calc.value = null
  calcInputs.value = null
  calcError.value = ''
  calcRows.value = []
  editing.value = true
  const sameDateSource = source && (kind === 'history' ? source.as_of_date === editDate.value : true)
  initialBonds = settingsFrom(sameDateSource ? source : null, kind)
  await runCalc(payload())
}

function cancelEdit() {
  editing.value = false
  calc.value = null
  calcRows.value = []
}

let calcSeq = 0
async function runCalc(p: CalcPayload) {
  const my = ++calcSeq
  calculating.value = true
  try {
    const res = await api.calibrate(code.value, editDate.value, p)
    if (my !== calcSeq) return
    calcRows.value = res.bonds
    calc.value = res.result
    calcInputs.value = res.inputs
    calcError.value = ''
    if (!cashflowSecid.value) cashflowSecid.value = res.bonds.find((b) => b.cashflows?.length)?.secid ?? null
  } catch (e: any) {
    if (my === calcSeq) calcError.value = e.message
  } finally {
    if (my === calcSeq) calculating.value = false
  }
}

const recalc = () => runCalc(payload())

let autoTimer: number | undefined
function scheduleRecalc() {
  if (!editing.value || !autoCalc.value) return
  clearTimeout(autoTimer)
  autoTimer = window.setTimeout(recalc, 500)
}
watch(params, scheduleRecalc, { deep: true })

async function refreshMarket() {
  refreshing.value = true
  try {
    const r = await api.refreshMarket(code.value)
    if (r.errors?.length) ElMessage.warning(`Не все данные получены: ${r.errors.join('; ')}`)
    else ElMessage.success(`Котировки обновлены (${r.snapshots} бумаг)`)
    await recalc()
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    refreshing.value = false
  }
}

async function save() {
  saving.value = true
  try {
    if (editKind.value === 'state') {
      state.value = await api.state.save(code.value, editDate.value, comment.value, payload())
      ElMessage.success(`Текущая кривая на ${formatDate(editDate.value)} сохранена`)
    } else {
      const snap = await api.history.save(code.value, editDate.value, comment.value.trim(), payload())
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

const exporting = ref(false)
async function exportExcel() {
  exporting.value = true
  try {
    if (editing.value) await api.excel.calculation(code.value, editDate.value, payload())
    else if (tab.value === 'current') await api.excel.state(code.value)
    else if (historySnap.value) await api.excel.history(code.value, historySnap.value.as_of_date, historySnap.value.version)
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    exporting.value = false
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
watch([chartView, compareOn], () => {
  if (!prefsLoaded) return
  clearTimeout(prefsTimer)
  prefsTimer = window.setTimeout(() => platform.preferences.set(PREFS_KEY, { view: chartView.value, compare: compareOn.value }), 400)
})
watch(displayedAsOf, loadComparisons)

onMounted(async () => {
  try {
    const [meta, prefs] = await Promise.all([api.meta(), platform.preferences.get<{ view?: typeof chartView.value; compare?: CompareKey[] }>(PREFS_KEY)])
    today.value = meta.today
    if (prefs?.view) chartView.value = prefs.view
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
.small {
  font-size: 12px;
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
  grid-template-columns: 380px minmax(0, 1fr);
}
.body.with-dates {
  grid-template-columns: 270px minmax(0, 1fr);
}
.side.editor {
  position: sticky;
  top: 0;
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
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
