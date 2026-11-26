<template>
  <div v-if="entity" class="page">
    <div class="head">
      <div>
        <h2>{{ entity.code }} <small>{{ entity.name }}</small></h2>
        <div class="muted">
          Кредитная кривая <router-link :to="`/credit/${encodeURIComponent(entity.credit_curve_code)}`">{{ entity.credit_curve_code }}</router-link>
          · стандартный купон {{ entity.standard_coupon_bp }} б.п. · до {{ entity.max_tenor }}
          <template v-if="entity.recovery_override != null"> · RR {{ Math.round(entity.recovery_override * 100) }}% (задан для CDS)</template>
          <template v-if="entity.doc_basis_bp"> · базис Сбера {{ entity.doc_basis_bp }} б.п.</template>
        </div>
      </div>
      <div class="spacer" />
      <router-link :to="{ path: '/credit/cds/calculator', query: { entity: entity.code } }">
        <el-button><el-icon><Operation /></el-icon>&nbsp;Калькулятор контракта</el-button>
      </router-link>
      <el-button v-if="isOperator" @click="dialogOpen = true"><el-icon><EditPen /></el-icon>&nbsp;Параметры</el-button>
    </div>

    <el-tabs v-model="tab" @tab-change="onTab">
      <el-tab-pane label="Текущая кривая" name="current" />
      <el-tab-pane label="История" name="history" />
    </el-tabs>

    <div class="context">
      <template v-if="snap">
        <span>CDS-кривая на <b>{{ formatDate(snap.as_of_date) }}</b></span>
        <el-select v-if="tab === 'history'" v-model="historyKey" size="small" style="width: 240px" @change="loadHistoryItem">
          <el-option v-for="h in history" :key="h.as_of_date" :value="h.as_of_date" :label="`${formatDate(h.as_of_date)} · версий ${h.versions}${h.edited ? ' · задним числом' : ''}`" />
        </el-select>
        <el-tag v-if="snap.source === 'backdated'" type="warning" size="small">изменение задним числом</el-tag>
        <span class="muted">{{ snap.trigger_title }}, {{ snap.author }}, {{ formatDateTime(snap.saved_at) }}</span>
        <span class="muted">RR {{ Math.round(snap.result.recovery * 100) }}% · Rq {{ Math.round(snap.result.recovery_quote * 100) }}%
          · дисконтирование {{ snap.inputs.discount?.code }} от {{ formatDate(snap.inputs.discount?.as_of_date) }}</span>
      </template>
      <span v-else class="muted">{{ tab === 'current' ? 'CDS-кривая ещё не рассчитана' : 'Истории пока нет' }}</span>
      <div class="spacer" />
      <el-button v-if="isOperator && tab === 'current'" :loading="recalculating" @click="recalc"><el-icon><Refresh /></el-icon>&nbsp;Пересчитать</el-button>
      <el-tooltip v-if="snap" content="Excel с детализацией: графики платежей, интегралы ног защиты и купона, par spread и PUF — формулами">
        <el-button :loading="exporting" @click="exportExcel"><el-icon><Download /></el-icon>&nbsp;Excel</el-button>
      </el-tooltip>
    </div>

    <template v-if="snap">
      <el-alert v-for="w in snap.result.warnings" :key="w" :title="w" type="warning" :closable="false" class="mb" />
      <el-card shadow="never" class="mb">
        <template #header>
          <div class="card-head">
            <b>Котировки по стандартным срокам (СПФИ)</b>
            <span class="muted">Дата начала срока {{ formatDate(snap.result.effective_date) }}<template v-if="rebate"> · ребейт с {{ formatDate(rebate.rebate_start) }}, {{ rebateDays }} дн.</template></span>
          </div>
        </template>
        <el-table :data="snap.result.table" size="small" border :row-class-name="({ row }: any) => (row.dropped ? 'dropped' : '')">
          <el-table-column prop="tenor" label="Срок" width="60" />
          <el-table-column label="Погашение" width="105"><template #default="{ row }">{{ formatDate(row.maturity) }}</template></el-table-column>
          <el-table-column label="Par из облигаций, б.п." width="110" align="right">
            <template #header><el-tooltip content="Модельный par spread стандартного контракта на кредитной кривой по облигациям"><span>Par из облигаций, б.п.</span></el-tooltip></template>
            <template #default="{ row }">{{ num(row.par_bond_bp, 2) }}</template>
          </el-table-column>
          <el-table-column label="Базис, б.п." width="80" align="right"><template #default="{ row }">{{ num(row.basis_bp, 1) }}</template></el-table-column>
          <el-table-column label="Par spread, б.п." width="100" align="right"><template #default="{ row }"><b>{{ num(row.par_spread_bp, 2) }}</b></template></el-table-column>
          <el-table-column label="QS, б.п." width="90" align="right">
            <template #header><el-tooltip content="Котируемый спред: спред плоской кривой с Rq, при котором PUF = 0 (справочно; цена на СПФИ — PUF)"><span>QS, б.п.</span></el-tooltip></template>
            <template #default="{ row }">{{ num(row.qs_bp, 2) }}</template>
          </el-table-column>
          <el-table-column v-for="c in snap.result.coupons" :key="c" :label="`PUF ${Math.round(c * 1e4)} б.п., %`" width="105" align="right">
            <template #header><el-tooltip content="Единоразовая премия в % номинала при купоне; плюс — платит покупатель защиты"><span>PUF {{ Math.round(c * 1e4) }} б.п., %</span></el-tooltip></template>
            <template #default="{ row }"><span :class="{ std: Math.round(c * 1e4) === entity!.standard_coupon_bp }">{{ num(row.puf[String(Math.round(c * 1e4))] * 100, 3) }}</span></template>
          </el-table-column>
          <el-table-column label="Дожитие" width="85" align="right"><template #default="{ row }">{{ num(row.survival, 4) }}</template></el-table-column>
          <el-table-column label="RPV01 чист." width="90" align="right"><template #default="{ row }">{{ num(row.rpv01_clean, 4) }}</template></el-table-column>
          <el-table-column label="λ участка, %" min-width="90" align="right"><template #default="{ row }">{{ row.lambda == null ? 'искл.' : num(row.lambda * 100, 3) }}</template></el-table-column>
        </el-table>
      </el-card>

      <div class="charts">
        <el-card shadow="never"><template #header>Спреды по срокам, б.п.</template><EChart :option="spreadOption" height="280px" /></el-card>
        <el-card shadow="never"><template #header>Интенсивность дефолта и дожитие</template><EChart :option="hazardOption" height="280px" /></el-card>
      </div>

      <el-card shadow="never" class="mt">
        <template #header>
          <div class="card-head">
            <b>График платежей стандартного контракта</b>
            <el-radio-group v-model="scheduleTenor" size="small">
              <el-radio-button v-for="r in snap.result.table" :key="r.tenor" :value="r.tenor">{{ r.tenor }}</el-radio-button>
            </el-radio-group>
            <span class="muted">номинал</span>
            <AmountInput v-model="notional" size="small" width="150px" inline-caption />
          </div>
        </template>
        <CdsScheduleTable :contract="scheduleRow" :coupon="entity.standard_coupon_bp / 1e4" :notional="notional ?? 0" />
      </el-card>
    </template>

    <CdsEntityDialog :open="dialogOpen" :entity="entity" :tenors="tenors" @close="dialogOpen = false" @saved="reload" />
  </div>
  <div v-else v-loading="true" style="height: 300px" />
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { formatDate, formatDateTime, getPlatform, Role } from '@cds/platform'

import { api } from '../api'
import CdsEntityDialog from '../components/CdsEntityDialog.vue'
import AmountInput from '../components/AmountInput.vue'
import CdsScheduleTable from '../components/CdsScheduleTable.vue'
import EChart from '../components/EChart.vue'
import { num } from '../format'
import type { CdsEntity, CdsSnapshot } from '../types'

const route = useRoute()
const isOperator = getPlatform().hasRole(Role.Operator)
const code = computed(() => String(route.params.code))
const entity = ref<CdsEntity | null>(null)
const tab = ref<'current' | 'history'>('current')
const state = ref<CdsSnapshot | null>(null)
const historySnap = ref<CdsSnapshot | null>(null)
const history = ref<{ as_of_date: string; versions: number; edited: boolean }[]>([])
const historyKey = ref<string | null>(null)
const tenors = ref<string[]>([])
const scheduleTenor = ref('5Y')
const notional = ref<number | null>(100_000_000)
const dialogOpen = ref(false)
const recalculating = ref(false)
const exporting = ref(false)

const snap = computed(() => (tab.value === 'current' ? state.value : historySnap.value))
const scheduleRow = computed(() => snap.value?.result.table.find((r) => r.tenor === scheduleTenor.value) ?? snap.value?.result.table.at(-1) ?? null)
const rebate = computed(() => snap.value?.result.table[0]?.rebate_start ? snap.value.result.table[0] : null)
const rebateDays = computed(() => (rebate.value ? Math.round((new Date(rebate.value.rebate_end!).getTime() - new Date(rebate.value.rebate_start!).getTime()) / 86400000) : 0))

const spreadOption = computed(() => {
  const t = snap.value?.result.table ?? []
  return {
    animation: false,
    grid: { left: 50, right: 16, top: 30, bottom: 30 },
    legend: { top: 0 },
    tooltip: { trigger: 'axis', valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(2) : String(v)) },
    xAxis: { type: 'category', data: t.map((r) => r.tenor) },
    yAxis: { type: 'value', scale: true, name: 'б.п.' },
    series: [
      { name: 'Par spread CDS', type: 'line', data: t.map((r) => r.par_spread_bp), lineStyle: { width: 2, color: '#1f4e79' }, itemStyle: { color: '#1f4e79' } },
      { name: 'QS', type: 'line', data: t.map((r) => r.qs_bp), lineStyle: { type: 'dashed', color: '#e6a23c' }, itemStyle: { color: '#e6a23c' } },
      { name: 'Par из облигаций', type: 'scatter', data: t.map((r) => r.par_bond_bp), itemStyle: { color: '#909399' } },
    ],
  }
})

const hazardOption = computed(() => {
  const m = snap.value?.result.monthly ?? []
  return {
    animation: false,
    grid: { left: 50, right: 50, top: 30, bottom: 30 },
    legend: { top: 0 },
    tooltip: { trigger: 'axis', valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(4) : String(v)) },
    xAxis: { type: 'time' },
    yAxis: [{ type: 'value', name: 'λ, %', scale: true }, { type: 'value', name: 'Q', scale: true, position: 'right' }],
    series: [
      { name: 'λ CDS-кривой', type: 'line', step: 'end', showSymbol: false, data: m.map((x) => [x.date, x.hazard * 100]), lineStyle: { width: 2, color: '#1f4e79' }, itemStyle: { color: '#1f4e79' } },
      { name: 'λ облигаций', type: 'line', step: 'end', showSymbol: false, data: m.map((x) => [x.date, x.bond_hazard * 100]), lineStyle: { color: '#909399' }, itemStyle: { color: '#909399' } },
      { name: 'Дожитие Q', type: 'line', yAxisIndex: 1, showSymbol: false, data: m.map((x) => [x.date, x.survival]), lineStyle: { type: 'dashed', color: '#67c23a' }, itemStyle: { color: '#67c23a' } },
    ],
  }
})

async function loadState() {
  try {
    state.value = await api.cds.state(code.value)
  } catch (e: any) {
    if (e.status !== 404) ElMessage.error(e.message)
    state.value = null
  }
}

async function onTab() {
  if (tab.value === 'history') {
    history.value = await api.cds.history(code.value)
    if (!historyKey.value && history.value.length) historyKey.value = history.value[0].as_of_date
    await loadHistoryItem()
  }
}

async function loadHistoryItem() {
  historySnap.value = historyKey.value ? await api.cds.historyItem(code.value, historyKey.value) : null
}

async function recalc() {
  recalculating.value = true
  try {
    state.value = await api.cds.recalculate(code.value)
    ElMessage.success('CDS-кривая пересчитана')
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    recalculating.value = false
  }
}

async function exportExcel() {
  exporting.value = true
  try {
    if (tab.value === 'current') await api.cds.excel.state(code.value)
    else if (historySnap.value) await api.cds.excel.history(code.value, historySnap.value.as_of_date, historySnap.value.version)
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    exporting.value = false
  }
}

async function reload() {
  entity.value = await api.cds.entity(code.value)
  await loadState()
}

onMounted(async () => {
  try {
    const meta = await api.cds.meta()
    tenors.value = meta.settings.tenors
    await reload()
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
.context {
  display: flex;
  align-items: center;
  gap: 12px;
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.charts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.mb {
  margin-bottom: 12px;
}
.mt {
  margin-top: 12px;
}
.std {
  font-weight: 700;
}
:deep(.dropped) {
  color: var(--el-color-danger);
}
</style>
