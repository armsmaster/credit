<template>
  <div>
    <div class="page-toolbar">
      <h2>Сделки CDS</h2>
      <span class="muted">оценка на</span>
      <el-date-picker v-model="date" type="date" value-format="YYYY-MM-DD" :clearable="false" style="width: 140px" @change="load" />
      <el-tooltip v-if="meta" :content="`Предыдущий рабочий день: ${formatDate(meta.previous_business_day)}`">
        <el-button :type="date === meta.previous_business_day ? 'primary' : 'default'" plain @click="setDate(meta.previous_business_day)">
          <el-icon><Back /></el-icon>&nbsp;пред. рабочий день
        </el-button>
      </el-tooltip>
      <el-button v-if="meta && date !== meta.today" link type="primary" @click="setDate(meta.today)">сегодня</el-button>
      <div class="spacer" />
      <el-tooltip v-if="isOperator" content="Оценить все действующие на дату сделки сейчас — по последней CDS-кривой не позже даты">
        <el-button :loading="revaluing" @click="revalueAll"><el-icon><Refresh /></el-icon>&nbsp;Оценить на дату</el-button>
      </el-tooltip>
      <el-button v-if="isOperator" type="primary" @click="router.push('/credit/cds/trades/new')"><el-icon><Plus /></el-icon>&nbsp;Новая сделка</el-button>
    </div>

    <div class="filters">
      <el-input v-model="filters.q" clearable placeholder="External ID или комментарий" style="width: 230px" @input="debouncedLoad">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-select v-model="filters.entity" clearable filterable placeholder="контрольное лицо" style="width: 200px" @change="load">
        <el-option v-for="e in meta?.entities ?? []" :key="e.code" :value="e.code" :label="`${e.code} — ${e.name}`" />
      </el-select>
      <el-select v-model="filters.tags" multiple filterable remote :remote-method="loadTags" :loading="tagsLoading" collapse-tags collapse-tags-tooltip
        placeholder="теги" style="width: 240px" @focus="loadTags('')" @change="load">
        <el-option v-for="t in tagOptions" :key="t.name" :value="t.name" :label="t.name" />
      </el-select>
      <el-select v-model="filters.side" clearable placeholder="сторона" style="width: 150px" @change="load">
        <el-option value="buyer" label="покупатель" />
        <el-option value="seller" label="продавец" />
      </el-select>
      <el-radio-group v-model="filters.status" @change="load">
        <el-radio-button value="active">действующие</el-radio-button>
        <el-radio-button value="cancelled">аннулированные</el-radio-button>
        <el-radio-button value="all">все</el-radio-button>
      </el-radio-group>
    </div>

    <div v-if="data" class="totals">
      <span>Сделок <b>{{ data.totals.trades }}</b>, оценено на {{ formatDate(data.date) }} <b>{{ data.totals.valued }}</b></span>
      <span>NPV <b :class="sign(data.totals.npv)">{{ money(data.totals.npv) }} ₽</b></span>
      <span>CS01 <b :class="sign(data.totals.cs01)">{{ money(data.totals.cs01) }} ₽</b></span>
      <span>Номинал: куплено защиты <b>{{ amountShort(data.totals.notional_bought) || '0 ₽' }}</b>, продано <b>{{ amountShort(data.totals.notional_sold) || '0 ₽' }}</b></span>
    </div>

    <el-table v-loading="loading" :data="data?.items ?? []" size="small" border empty-text="Сделок нет" @row-dblclick="open">
      <el-table-column label="External ID" min-width="130" fixed>
        <template #default="{ row }">
          <router-link :to="`/credit/cds/trades/${row.id}`" class="link">{{ row.external_id }}</router-link>
          <el-tag v-if="row.status === 'cancelled'" size="small" type="info" class="ml">аннулирована</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="Контр. лицо" prop="entity_code" min-width="90" />
      <el-table-column label="Сторона" width="100">
        <template #default="{ row }"><el-tag size="small" :type="row.side === 'buyer' ? 'success' : 'warning'">{{ row.side === 'buyer' ? 'покупатель' : 'продавец' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="Номинал" min-width="88" align="right"><template #default="{ row }">{{ amountShort(row.notional) }}</template></el-table-column>
      <el-table-column label="Купон" width="70" align="right"><template #default="{ row }">{{ num(row.coupon_bp, 0) }}</template></el-table-column>
      <el-table-column label="Сделка" width="92"><template #default="{ row }">{{ formatDate(row.trade_date) }}</template></el-table-column>
      <el-table-column label="Экспирация" width="92"><template #default="{ row }">{{ formatDate(row.maturity) }}</template></el-table-column>
      <el-table-column label="Теги" min-width="110">
        <template #default="{ row }"><el-tag v-for="t in row.tags" :key="t" size="small" class="tag" @click.stop="addTag(t)">{{ t }}</el-tag></template>
      </el-table-column>
      <el-table-column label="NPV, ₽" min-width="118" align="right">
        <template #default="{ row }">
          <template v-if="row.valuation"><b :class="sign(row.valuation.npv)">{{ money(row.valuation.npv) }}</b></template>
          <span v-else class="muted">{{ lifeText(row) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="Накоп. купон, ₽" min-width="100" align="right">
        <template #default="{ row }">{{ row.valuation ? money(row.valuation.accrued) : '' }}</template>
      </el-table-column>
      <el-table-column label="Par, б.п." width="80" align="right">
        <template #default="{ row }">{{ row.valuation ? num(row.valuation.par_spread_bp, 1) : '' }}</template>
      </el-table-column>
      <el-table-column label="CS01, ₽" min-width="88" align="right">
        <template #default="{ row }">{{ row.valuation ? money(row.valuation.cs01) : '' }}</template>
      </el-table-column>
      <el-table-column label="Расчёт" min-width="128">
        <template #default="{ row }">
          <el-tooltip v-if="row.valuation" placement="left"
            :content="`${row.valuation.trigger_title}; CDS-кривая на ${formatDate(row.valuation.curve_date)}; версия сделки ${row.valuation.trade_version}`">
            <span class="calc">
              <el-tag size="small" :type="row.valuation.kind === 'eod' ? 'primary' : 'info'">{{ row.valuation.kind === 'eod' ? 'итог дня' : 'внутри дня' }}</el-tag>
              {{ time(row.valuation.calculated_at) }}
              <el-icon v-if="row.valuation.details.stale_days > 0" class="stale"><Warning /></el-icon>
            </span>
          </el-tooltip>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { formatDate, getPlatform, Role } from '@cds/platform'

import { api } from '../api'
import { amountShort, num } from '../format'
import type { TradeSearch, TradesMeta, TradeSummary } from '../types'

const route = useRoute()
const router = useRouter()
const isOperator = getPlatform().hasRole(Role.Operator)
const meta = ref<TradesMeta | null>(null)
const date = ref('')
const data = ref<TradeSearch | null>(null)
const loading = ref(false)
const revaluing = ref(false)
const filters = reactive({ q: '', entity: '', tags: [] as string[], side: '', status: 'active' })
const tagOptions = ref<{ name: string; trades: number }[]>([])
const tagsLoading = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined

const money = (v: number) => v.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const sign = (v: number) => (v > 0 ? 'pos' : v < 0 ? 'neg' : '')
const time = (iso: string) => new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })

function lifeText(row: TradeSummary) {
  if (row.status === 'cancelled') return '—'
  if (row.life === 'not_started') return 'ещё не заключена'
  if (row.life === 'matured') return 'истекла'
  return 'нет оценки'
}

async function load() {
  loading.value = true
  try {
    data.value = await api.trades.search({ date: date.value, q: filters.q, entity: filters.entity, tag: filters.tags, side: filters.side, status: filters.status })
    router.replace({ query: { ...route.query, date: date.value } })
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function debouncedLoad() {
  clearTimeout(timer)
  timer = setTimeout(load, 300)
}

function setDate(d: string) {
  date.value = d
  load()
}

function addTag(t: string) {
  if (!filters.tags.includes(t)) {
    filters.tags.push(t)
    load()
  }
}

function open(row: TradeSummary) {
  router.push(`/credit/cds/trades/${row.id}`)
}

async function loadTags(q: string) {
  tagsLoading.value = true
  try {
    tagOptions.value = await api.trades.tags(q || undefined)
  } finally {
    tagsLoading.value = false
  }
}

async function revalueAll() {
  revaluing.value = true
  try {
    const r = await api.trades.revalueAll(date.value)
    ElMessage.success(`Оценено сделок: ${r.valued}${r.skipped.length ? `; без оценки (нет CDS-кривой): ${r.skipped.length}` : ''}`)
    await load()
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    revaluing.value = false
  }
}

onMounted(async () => {
  try {
    meta.value = await api.trades.meta()
    date.value = (route.query.date as string) || meta.value.today
    await load()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
})
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
  margin: 0 8px 0 0;
}
.spacer {
  flex: 1;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.totals {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 24px;
  font-size: 13px;
  margin-bottom: 8px;
}
.muted {
  color: var(--el-text-color-secondary);
}
.link {
  color: var(--el-color-primary);
  text-decoration: none;
  font-weight: 600;
}
.ml {
  margin-left: 6px;
}
.tag {
  margin: 1px 4px 1px 0;
  cursor: pointer;
}
.calc {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
.stale {
  color: var(--el-color-warning);
}
.pos {
  color: var(--el-color-success);
}
.neg {
  color: var(--el-color-danger);
}
</style>
