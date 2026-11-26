<template>
  <div v-loading="loading">
    <template v-if="t">
      <div class="page-toolbar">
        <el-button text @click="router.push('/credit/cds/trades')"><el-icon><ArrowLeft /></el-icon></el-button>
        <h2>{{ t.external_id }}</h2>
        <el-tag :type="t.side === 'buyer' ? 'success' : 'warning'">{{ t.side === 'buyer' ? 'покупка защиты' : 'продажа защиты' }}</el-tag>
        <router-link :to="`/credit/cds/${t.entity_code}`" class="link">{{ t.entity_code }} — {{ t.entity_name }}</router-link>
        <el-tag v-if="t.status === 'cancelled'" type="info">аннулирована</el-tag>
        <el-tag size="small" type="info">версия {{ t.version }}</el-tag>
        <el-tag v-for="tag in t.tags" :key="tag" size="small" effect="plain">{{ tag }}</el-tag>
        <div class="spacer" />
        <template v-if="isOperator">
          <el-button v-if="t.status === 'active'" :loading="busy" @click="revalue"><el-icon><Refresh /></el-icon>&nbsp;Оценить сейчас</el-button>
          <el-button v-if="t.status === 'active'" type="primary" @click="router.push(`/credit/cds/trades/${t.id}/edit`)"><el-icon><Edit /></el-icon>&nbsp;Изменить</el-button>
          <el-button v-if="t.status === 'active'" type="danger" plain :loading="busy" @click="cancel">Аннулировать</el-button>
          <el-button v-else :loading="busy" @click="restore">Восстановить</el-button>
        </template>
      </div>

      <el-card shadow="never" class="mb">
        <div v-if="last" class="kpis">
          <div class="kpi"><span>NPV на {{ formatDate(last.valuation_date) }}</span><b :class="sign(last.npv)">{{ money(last.npv) }} ₽</b></div>
          <div class="kpi"><span>Чистая стоимость</span><b>{{ money(last.clean) }} ₽</b></div>
          <div class="kpi"><span>Накопленный купон</span><b>{{ money(last.accrued) }} ₽</b></div>
          <div class="kpi"><span>Par spread остатка</span><b>{{ num(last.par_spread_bp, 2) }} б.п.</b></div>
          <div class="kpi"><span>CS01 (≈ N·RPV01·1 б.п.)</span><b>{{ money(last.cs01) }} ₽</b></div>
          <div class="kpi"><span>Вероятность дефолта до экспирации</span><b>{{ num(last.details.pd * 100, 3) }}%</b></div>
          <div v-if="last.details.next_payment" class="kpi">
            <span>Ближайший купон {{ formatDate(last.details.next_payment.date) }}</span><b>{{ money(last.details.next_payment.amount) }} ₽</b>
          </div>
        </div>
        <el-empty v-else description="Оценки пока нет: нет CDS-кривой контрольного лица или сделка не действует сегодня" :image-size="60" />
        <div v-if="last" class="muted small mt">
          {{ last.kind === 'eod' ? 'Итог дня' : 'Расчёт внутри дня' }} {{ dateTime(last.calculated_at) }} ({{ last.trigger_title }}, {{ last.calculated_by }});
          CDS-кривая на {{ formatDate(last.curve_date) }}<template v-if="last.details.stale_days > 0"> — перенесена на дату оценки ({{ last.details.stale_days }} дн.)</template>,
          дисконтирование {{ last.details.discount?.code }}; RR {{ Math.round(last.details.recovery * 100) }}%. Знак — со стороны сделки.
        </div>
      </el-card>

      <div class="grid">
        <el-card shadow="never">
          <template #header><b>Оценка по датам</b></template>
          <EChart v-if="t.valuations.length" :option="chartOption" height="280px" />
          <el-empty v-else description="Нет оценок" :image-size="60" />
        </el-card>
        <el-card shadow="never">
          <template #header><b>Условия</b></template>
          <dl class="terms">
            <dt>Номинал</dt><dd>{{ money(t.notional) }} ₽ <span class="muted">({{ amountShort(t.notional) }})</span></dd>
            <dt>Купон</dt><dd>{{ num(t.coupon_bp, 2) }} б.п.</dd>
            <dt>Дата сделки</dt><dd>{{ formatDate(t.trade_date) }}</dd>
            <dt>Начало срока</dt><dd>{{ formatDate(t.effective_date) }}</dd>
            <dt>Экспирация</dt><dd>{{ formatDate(t.maturity) }}</dd>
            <dt>Ребейт</dt><dd>{{ t.rebate ? `да, ${money(t.schedule.rebate_amount)} ₽` : 'нет' }}</dd>
            <dt>Расписание</dt><dd>{{ scheduleText }}<el-tag v-if="t.schedule_edited" size="small" type="warning" class="ml">ручные правки</el-tag></dd>
            <dt>Введена</dt><dd>{{ dateTime(t.created_at) }}, {{ t.created_by }}</dd>
            <dt>Изменена</dt><dd>{{ dateTime(t.updated_at) }}, {{ t.updated_by }}</dd>
            <template v-if="t.comment"><dt>Комментарий</dt><dd>{{ t.comment }}</dd></template>
          </dl>
        </el-card>
      </div>

      <el-card shadow="never" class="mt">
        <template #header>
          <div class="card-head">
            <b>Значения оценки</b>
            <span class="muted small">по одному значению на дату (последний расчёт даты); всего расчётов {{ t.valuation_records }}</span>
            <div class="spacer" />
            <el-switch v-model="allRecords" active-text="все расчёты внутри дня" @change="loadRecords" />
          </div>
        </template>
        <el-table :data="tableRows" size="small" border max-height="420" empty-text="Нет оценок">
          <el-table-column label="Дата" width="100"><template #default="{ row }">{{ formatDate(row.valuation_date) }}</template></el-table-column>
          <el-table-column label="Вид" width="100">
            <template #default="{ row }"><el-tag size="small" :type="row.kind === 'eod' ? 'primary' : 'info'">{{ row.kind === 'eod' ? 'итог дня' : 'внутри дня' }}</el-tag></template>
          </el-table-column>
          <el-table-column label="Рассчитано" min-width="130"><template #default="{ row }">{{ dateTime(row.calculated_at) }}</template></el-table-column>
          <el-table-column label="Причина" prop="trigger_title" min-width="150" />
          <el-table-column label="Версия" prop="trade_version" width="70" align="right" />
          <el-table-column label="Кривая на" width="100"><template #default="{ row }">{{ formatDate(row.curve_date) }}</template></el-table-column>
          <el-table-column label="NPV, ₽" min-width="130" align="right"><template #default="{ row }"><b :class="sign(row.npv)">{{ money(row.npv) }}</b></template></el-table-column>
          <el-table-column label="Чистая, ₽" min-width="130" align="right"><template #default="{ row }">{{ money(row.clean) }}</template></el-table-column>
          <el-table-column label="Накоп. купон, ₽" min-width="120" align="right"><template #default="{ row }">{{ money(row.accrued) }}</template></el-table-column>
          <el-table-column label="Par, б.п." width="84" align="right"><template #default="{ row }">{{ num(row.par_spread_bp, 2) }}</template></el-table-column>
          <el-table-column label="CS01, ₽" min-width="110" align="right"><template #default="{ row }">{{ money(row.cs01) }}</template></el-table-column>
        </el-table>
      </el-card>

      <div class="grid even mt">
        <el-card shadow="never">
          <template #header><b>График платежей</b> <span class="muted small">купоны со стороны сделки; прошедшие — серым</span></template>
          <el-table :data="t.schedule.rows" size="small" border :row-class-name="pastClass" max-height="420">
            <el-table-column type="index" label="№" width="44" />
            <el-table-column label="Начало" min-width="96"><template #default="{ row }">{{ formatDate(row.accrual_start) }}</template></el-table-column>
            <el-table-column label="Конец" min-width="96"><template #default="{ row }">{{ formatDate(row.accrual_end) }}</template></el-table-column>
            <el-table-column label="Платёж" min-width="96"><template #default="{ row }">{{ formatDate(row.pay_date) }}</template></el-table-column>
            <el-table-column label="Дней" prop="days" width="56" align="right" />
            <el-table-column label="Купон, ₽" min-width="120" align="right">
              <template #default="{ row }">{{ money((t.side === 'buyer' ? -1 : 1) * (row.amount ?? 0)) }}<el-icon v-if="row.manual" class="manual"><EditPen /></el-icon></template>
            </el-table-column>
          </el-table>
        </el-card>
        <el-card shadow="never">
          <template #header><b>Версии</b></template>
          <el-table :data="t.versions" size="small" border max-height="420">
            <el-table-column label="№" prop="version" width="50" />
            <el-table-column label="Сохранена" min-width="140"><template #default="{ row }">{{ dateTime(row.created_at) }}</template></el-table-column>
            <el-table-column label="Автор" prop="created_by" min-width="100" />
            <el-table-column label="Номинал" min-width="100" align="right"><template #default="{ row }">{{ amountShort(row.notional) }}</template></el-table-column>
            <el-table-column label="Купон" width="64" align="right"><template #default="{ row }">{{ num(row.coupon_bp, 0) }}</template></el-table-column>
            <el-table-column label="Экспирация" width="96"><template #default="{ row }">{{ formatDate(row.maturity) }}</template></el-table-column>
          </el-table>
        </el-card>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { formatDate, getPlatform, Role } from '@cds/platform'

import { api } from '../api'
import EChart from '../components/EChart.vue'
import { amountShort, num } from '../format'
import type { TradeDetail, TradeRow, TradeValuation } from '../types'

const route = useRoute()
const router = useRouter()
const isOperator = getPlatform().hasRole(Role.Operator)
const t = ref<TradeDetail | null>(null)
const loading = ref(false)
const busy = ref(false)
const allRecords = ref(false)
const records = ref<TradeValuation[]>([])

const money = (v: number) => v.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const sign = (v: number) => (v > 0 ? 'pos' : v < 0 ? 'neg' : '')
const dateTime = (iso: string) => new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
const today = new Date().toISOString().slice(0, 10)

const last = computed(() => t.value?.latest_valuation ?? null)
const tableRows = computed(() => (allRecords.value ? records.value : [...(t.value?.valuations ?? [])].reverse()))
const BDC: Record<string, string> = { following: 'Следующий', modified_following: 'Мод. следующий', preceding: 'Предыдущий', unadjusted: 'без переноса' }
const scheduleText = computed(() => {
  const p = t.value?.schedule_params
  if (!p) return ''
  const base = p.mode === 'standard' ? 'стандартные даты CDS' : `каждые ${p.frequency_months} мес., ${p.direction === 'backward' ? 'назад от экспирации' : 'вперёд от начала'}, ${p.stub === 'short' ? 'короткий' : 'длинный'} неполный период, ${BDC[p.bdc]}`
  return `${base}; ${p.calendar}, ${p.day_count}`
})

const chartOption = computed(() => {
  const vals = t.value?.valuations ?? []
  return {
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${money(v)} ₽` },
    legend: { data: ['NPV', 'Чистая'], top: 0 },
    grid: { left: 90, right: 20, top: 30, bottom: 30 },
    xAxis: { type: 'time', axisLabel: { formatter: '{dd}.{MM}' } },
    yAxis: { type: 'value', scale: true, axisLabel: { formatter: (v: number) => amountShort(v).replace(' ₽', '') } },
    series: [
      { name: 'NPV', type: 'line', showSymbol: true, symbolSize: 6, data: vals.map((v) => [v.valuation_date, v.npv]) },
      { name: 'Чистая', type: 'line', showSymbol: false, lineStyle: { type: 'dashed' }, data: vals.map((v) => [v.valuation_date, v.clean]) },
    ],
  }
})

function pastClass({ row }: { row: TradeRow }) {
  return row.pay_date <= today ? 'past-row' : ''
}

async function load() {
  loading.value = true
  try {
    t.value = await api.trades.get(route.params.id as string)
    if (allRecords.value) await loadRecords()
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

async function loadRecords() {
  if (!allRecords.value || !t.value) return
  records.value = await api.trades.valuations(t.value.id)
}

async function act(fn: () => Promise<TradeDetail>, msg: string) {
  busy.value = true
  try {
    t.value = await fn()
    await loadRecords()
    ElMessage.success(msg)
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    busy.value = false
  }
}

const revalue = () => act(() => api.trades.revalue(t.value!.id), 'Сделка оценена')
const restore = () => act(() => api.trades.restore(t.value!.id), 'Сделка восстановлена')
async function cancel() {
  try {
    await ElMessageBox.confirm(`Аннулировать сделку ${t.value!.external_id}? Она перестанет оцениваться; восстановить можно в любой момент.`,
      'Аннулирование', { type: 'warning', confirmButtonText: 'Аннулировать', cancelButtonText: 'Отмена' })
  } catch {
    return
  }
  await act(() => api.trades.cancel(t.value!.id), 'Сделка аннулирована')
}

onMounted(load)
</script>

<style scoped>
.page-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.page-toolbar h2 {
  margin: 0;
}
.spacer {
  flex: 1;
}
.link {
  color: var(--el-color-primary);
  text-decoration: none;
}
.kpis {
  display: flex;
  flex-wrap: wrap;
  gap: 18px 28px;
}
.kpi {
  display: flex;
  flex-direction: column;
}
.kpi span {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.grid {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}
.grid.even {
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
}
.terms {
  display: grid;
  grid-template-columns: 130px 1fr;
  gap: 6px 12px;
  margin: 0;
  font-size: 13px;
}
.terms dt {
  color: var(--el-text-color-secondary);
}
.terms dd {
  margin: 0;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.muted {
  color: var(--el-text-color-secondary);
}
.small {
  font-size: 12px;
}
.ml {
  margin-left: 6px;
}
.mt {
  margin-top: 12px;
}
.mb {
  margin-bottom: 12px;
}
.pos {
  color: var(--el-color-success);
}
.neg {
  color: var(--el-color-danger);
}
.manual {
  margin-left: 4px;
  color: var(--el-color-warning);
}
:deep(.past-row) td {
  color: var(--el-text-color-placeholder);
}
</style>
