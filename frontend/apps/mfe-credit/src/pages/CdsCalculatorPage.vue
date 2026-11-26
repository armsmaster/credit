<template>
  <div class="page">
    <div class="page-toolbar">
      <h2>Калькулятор CDS</h2>
      <span class="muted">Любой контракт на CDS-кривой контрольного лица: график платежей, ребейт, par spread, PUF/QS и NPV предложенных условий</span>
    </div>
    <div class="layout">
      <el-card shadow="never">
        <el-form label-position="top" size="default">
          <el-form-item label="Контрольное лицо">
            <el-select v-model="req.entity_code" filterable style="width: 100%" @change="onEntity">
              <el-option v-for="e in entities" :key="e.code" :value="e.code" :label="`${e.code} — ${e.name}`" :disabled="!e.state" />
            </el-select>
          </el-form-item>
          <el-form-item label="Профиль конвенций">
            <el-radio-group v-model="req.profile" @change="onProfile">
              <el-radio-button value="SPFI_STD">СПФИ</el-radio-button>
              <el-radio-button value="SBER_OTC">Сбер (OTC)</el-radio-button>
            </el-radio-group>
            <div class="hint">{{ req.profile === 'SPFI_STD' ? 'Стандартный купон + upfront, дата начала T+1 р.д., ребейт' : 'Par-купон с даты сделки, короткий первый период, без ребейта' }}</div>
          </el-form-item>
          <div class="two">
            <el-form-item label="Кривая на дату">
              <el-date-picker v-model="req.as_of_date" type="date" value-format="YYYY-MM-DD" placeholder="текущая" style="width: 100%" />
            </el-form-item>
            <el-form-item label="Дата сделки T">
              <el-date-picker v-model="req.trade_date" type="date" value-format="YYYY-MM-DD" placeholder="= дата кривой" style="width: 100%" />
            </el-form-item>
          </div>
          <el-form-item label="Плановая дата прекращения">
            <div class="maturity">
              <el-radio-group v-model="maturityMode">
                <el-radio-button value="tenor">стандартный срок</el-radio-button>
                <el-radio-button value="date">дата</el-radio-button>
              </el-radio-group>
              <el-select v-if="maturityMode === 'tenor'" v-model="req.tenor" class="grow">
                <el-option v-for="t in tenors" :key="t" :value="t" />
              </el-select>
              <el-date-picker v-else v-model="req.maturity" type="date" value-format="YYYY-MM-DD" class="grow" />
            </div>
          </el-form-item>
          <div class="two">
            <el-form-item label="Купон, б.п.">
              <el-input-number v-model="req.coupon_bp" :min="0" :max="10000" :step="5" controls-position="right" />
            </el-form-item>
            <el-form-item label="Номинал, ₽">
              <AmountInput v-model="req.notional" />
            </el-form-item>
            <el-form-item label="Сторона" class="full">
              <el-radio-group v-model="req.side">
                <el-radio-button value="buyer">покупатель</el-radio-button>
                <el-radio-button value="seller">продавец</el-radio-button>
              </el-radio-group>
            </el-form-item>
          </div>
          <div v-if="req.profile === 'SPFI_STD'" class="quote">
            <div class="quote-head">
              <el-tooltip placement="top" :content="QUOTE_HELP"><span>Цена сделки или заявки <el-icon><QuestionFilled /></el-icon></span></el-tooltip>
              <div class="spacer" />
              <el-button v-if="quoteSource" link size="small" @click="clearQuote">очистить</el-button>
            </div>
            <div class="two">
              <el-form-item :label="quoteSource === 'puf' ? 'QS, б.п. ← из PUF' : 'QS, б.п.'">
                <el-input-number v-model="req.qs_bp" :min="0.01" :max="20000" :step="1" :precision="2" controls-position="right"
                  placeholder="—" style="width: 100%" :class="{ derived: quoteSource === 'puf' }" @input="onQuoteInput('qs')" />
              </el-form-item>
              <el-form-item :label="quoteSource === 'qs' ? 'Upfront (PUF), % ← из QS' : 'Upfront (PUF), %'">
                <el-input-number v-model="req.upfront_pct" :min="-100" :max="100" :step="0.01" :precision="4" controls-position="right"
                  placeholder="—" style="width: 100%" :class="{ derived: quoteSource === 'qs' }" @input="onQuoteInput('puf')" />
              </el-form-item>
            </div>
            <div v-if="quoteError" class="quote-error">{{ quoteError }}</div>
            <div v-else-if="quote" class="hint">
              Upfront {{ money(quote.upfront_amount) }} ₽, с ребейтом {{ money(quote.initial_payment) }} ₽ ({{ formatDate(quote.upfront_settle_date) }});
              плоская λ {{ num(quote.flat_lambda * 100, 3) }}% при Rq {{ Math.round(quote.recovery_quote * 100) }}%
            </div>
            <div v-else class="hint">Введите QS или upfront — второе посчитается автоматически. Пусто — NPV по модели без upfront.</div>
          </div>
          <el-form-item label="Recovery, % (пусто — по кривой)">
            <el-input-number v-model="recoveryPct" :min="0" :max="95" :step="5" controls-position="right" placeholder="по кривой" />
          </el-form-item>
          <div class="actions">
            <el-button type="primary" :loading="calculating" :disabled="!req.entity_code" @click="calculate">Рассчитать</el-button>
            <el-button :disabled="!result" :loading="exporting" @click="exportExcel"><el-icon><Download /></el-icon>&nbsp;Excel</el-button>
          </div>
          <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon />
        </el-form>
      </el-card>

      <div v-if="result">
        <el-alert v-for="w in result.warnings" :key="w" :title="w" type="warning" :closable="false" class="mb" />
        <el-card shadow="never" class="mb">
          <div class="kpis">
            <div class="kpi"><span>Par spread</span><b>{{ num(result.par_spread_bp, 2) }} б.п.</b></div>
            <div class="kpi"><span>PUF модели при {{ num(result.coupon_bp, 0) }} б.п.</span><b>{{ num(result.puf_model * 100, 4) }}%</b></div>
            <div class="kpi"><span>QS</span><b>{{ num(result.qs_bp, 2) }} б.п.</b></div>
            <div class="kpi"><span>NPV {{ result.side === 'buyer' ? 'покупателя' : 'продавца' }}</span><b :class="result.npv >= 0 ? 'pos' : 'neg'">{{ money(result.npv) }} ₽</b></div>
            <div class="kpi" v-if="result.profile === 'SPFI_STD'"><span>Ребейт</span><b>{{ money(result.rebate_amount) }} ₽</b></div>
            <div class="kpi" v-if="result.profile === 'SPFI_STD'"><span>Первоначальный платёж (модель)</span><b>{{ money(result.initial_payment_model) }} ₽</b></div>
            <div class="kpi" v-if="result.upfront_pct != null && result.trade_qs_bp != null">
              <span>Цена сделки ({{ result.quote_input === 'qs' ? 'QS → PUF' : 'PUF → QS' }})</span>
              <b>{{ num(result.upfront_pct, 4) }}% · {{ num(result.trade_qs_bp, 2) }} б.п.</b>
            </div>
            <div class="kpi" v-if="result.initial_payment_trade != null"><span>Первоначальный платёж (сделка)</span><b>{{ money(result.initial_payment_trade) }} ₽</b></div>
            <div class="kpi"><span>Вероятность дефолта до погашения</span><b>{{ num(result.pd * 100, 3) }}%</b></div>
            <div class="kpi"><span>Выплата при дефолте N·(1−R)</span><b>{{ money(result.jtd_buyer) }} ₽</b></div>
          </div>
          <div class="muted small mt8">
            Кривая на {{ formatDate(result.curve_date) }}, RR {{ Math.round(result.recovery * 100) }}%, конвенции {{ result.conventions.name }},
            купон {{ result.conventions.day_count }}, календарь {{ result.conventions.calendar }}. Все величины покупателя; у продавца — с обратным знаком.
          </div>
        </el-card>
        <el-card shadow="never">
          <template #header><b>График платежей до {{ formatDate(result.maturity) }}</b></template>
          <CdsScheduleTable :contract="result" :coupon="result.coupon_bp / 1e4" :standard-coupon="result.coupon_bp / 1e4" :notional="result.notional" />
        </el-card>
      </div>
      <el-empty v-else description="Задайте условия контракта и нажмите «Рассчитать»" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { formatDate } from '@cds/platform'

import { api } from '../api'
import AmountInput from '../components/AmountInput.vue'
import CdsScheduleTable from '../components/CdsScheduleTable.vue'
import { num } from '../format'
import type { CdsContractRequest, CdsContractResult, CdsEntity, CdsQuoteConversion } from '../types'

const QUOTE_HELP =
  'QS и upfront (PUF, % номинала, плюс — платит покупатель) пересчитываются друг в друга по конвенции ISDA: ' +
  'плоская кривая интенсивности с Rq, на которой QS — par spread, а PUF — upfront при купоне контракта. ' +
  'Дисконтирование — по кривой RUB даты CDS-кривой. Пусто — NPV по модели без upfront.'

const route = useRoute()
const entities = ref<CdsEntity[]>([])
const tenors = ref<string[]>(['6M', '1Y', '2Y', '3Y', '4Y', '5Y'])
const maturityMode = ref<'tenor' | 'date'>('tenor')
const req = ref<CdsContractRequest>({ entity_code: '', profile: 'SPFI_STD', side: 'buyer', tenor: '5Y', coupon_bp: 100, notional: 100_000_000, upfront_pct: null, qs_bp: null })
const result = ref<CdsContractResult | null>(null)
const error = ref('')
const calculating = ref(false)
const exporting = ref(false)

// trade quote: the field the user typed in (source) and the derived one
const quoteSource = ref<'qs' | 'puf' | null>(null)
const quote = ref<CdsQuoteConversion | null>(null)
const quoteError = ref('')
let quoteTimer: ReturnType<typeof setTimeout> | undefined
let quoteSeq = 0

const recoveryPct = computed({
  get: () => (req.value.recovery == null ? undefined : Math.round(req.value.recovery * 1000) / 10),
  set: (v: number | undefined | null) => {
    req.value.recovery = v == null ? null : v / 100
  },
})
const money = (v: number) => v.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

function body(): CdsContractRequest {
  const spfi = req.value.profile === 'SPFI_STD'
  return {
    ...req.value,
    tenor: maturityMode.value === 'tenor' ? req.value.tenor : null,
    maturity: maturityMode.value === 'date' ? req.value.maturity : null,
    upfront_pct: spfi ? (req.value.upfront_pct ?? null) : null,
    qs_bp: spfi ? (req.value.qs_bp ?? null) : null,
    quote_input: spfi ? quoteSource.value : null,
    as_of_date: req.value.as_of_date || null,
    trade_date: req.value.trade_date || null,
  }
}

const isEmpty = (v: number | null | undefined) => v == null || Number.isNaN(v)

function setDerived(src: 'qs' | 'puf', value: number | null) {
  if (src === 'qs') req.value.upfront_pct = value
  else req.value.qs_bp = value
}

function onQuoteInput(src: 'qs' | 'puf') {
  quoteSource.value = src
  scheduleQuote()
}

function scheduleQuote(delay = 350) {
  clearTimeout(quoteTimer)
  quoteTimer = setTimeout(convertQuote, delay)
}

async function convertQuote() {
  const src = quoteSource.value
  if (!src || req.value.profile !== 'SPFI_STD' || !req.value.entity_code) return
  const my = ++quoteSeq
  if (isEmpty(src === 'qs' ? req.value.qs_bp : req.value.upfront_pct)) {
    setDerived(src, null)
    quoteSource.value = null
    quote.value = null
    quoteError.value = ''
    return
  }
  try {
    const r = await api.cds.convertQuote(body())
    if (my !== quoteSeq) return // a newer input is already being converted
    quote.value = r
    quoteError.value = ''
    setDerived(src, src === 'qs' ? Math.round(r.upfront_pct * 1e4) / 1e4 : Math.round(r.qs_bp * 100) / 100)
  } catch (e: any) {
    if (my !== quoteSeq) return
    quote.value = null
    quoteError.value = e.message
    setDerived(src, null)
  }
}

function clearQuote() {
  quoteSeq++
  clearTimeout(quoteTimer)
  req.value.qs_bp = null
  req.value.upfront_pct = null
  quoteSource.value = null
  quote.value = null
  quoteError.value = ''
}

// the derived value depends on the contract: recompute it when the terms change
watch(
  [
    () => req.value.entity_code,
    () => req.value.as_of_date,
    () => req.value.trade_date,
    () => req.value.tenor,
    () => req.value.maturity,
    maturityMode,
    () => req.value.coupon_bp,
    () => req.value.notional,
  ],
  () => {
    if (quoteSource.value) scheduleQuote(500)
  },
)
onBeforeUnmount(() => clearTimeout(quoteTimer))

function onEntity() {
  const e = entities.value.find((x) => x.code === req.value.entity_code)
  if (e && req.value.profile === 'SPFI_STD') req.value.coupon_bp = e.standard_coupon_bp
}

function onProfile() {
  if (req.value.profile === 'SBER_OTC') {
    maturityMode.value = 'date'
    clearQuote()
  } else onEntity()
}

async function calculate() {
  calculating.value = true
  error.value = ''
  try {
    result.value = await api.cds.calculate(body())
  } catch (e: any) {
    error.value = e.message
    result.value = null
  } finally {
    calculating.value = false
  }
}

async function exportExcel() {
  exporting.value = true
  try {
    await api.cds.excel.contract(body())
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    exporting.value = false
  }
}

onMounted(async () => {
  try {
    const [meta, list] = await Promise.all([api.cds.meta(), api.cds.entities()])
    tenors.value = meta.settings.tenors
    entities.value = list
    const preset = (route.query.entity as string) || list.find((e) => e.state)?.code || ''
    req.value.entity_code = preset
    onEntity()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
})
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
.layout {
  display: grid;
  grid-template-columns: 440px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}
.two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 12px;
}
.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.3;
  margin-top: 4px;
}
.maturity {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}
.maturity .el-radio-group {
  flex: none;
}
.maturity .grow {
  flex: 1;
  min-width: 0;
}
.quote {
  border-top: 1px dashed var(--el-border-color);
  padding-top: 10px;
  margin-bottom: 12px;
}
.quote-head {
  display: flex;
  align-items: center;
  font-size: 14px;
  color: var(--el-text-color-regular);
  margin-bottom: 6px;
}
.quote .el-form-item {
  margin-bottom: 6px;
}
.quote-error {
  font-size: 12px;
  color: var(--el-color-danger);
  line-height: 1.3;
}
.derived :deep(.el-input__wrapper) {
  background: var(--el-fill-color-light);
}
.spacer {
  flex: 1;
}
.full {
  grid-column: 1 / -1;
}
.actions {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
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
.pos {
  color: var(--el-color-success);
}
.neg {
  color: var(--el-color-danger);
}
.muted {
  color: var(--el-text-color-secondary);
}
.small {
  font-size: 12px;
}
.mt8 {
  margin-top: 8px;
}
.mb {
  margin-bottom: 12px;
}
</style>
