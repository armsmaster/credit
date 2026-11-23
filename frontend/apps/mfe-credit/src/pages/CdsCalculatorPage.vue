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
            <el-radio-group v-model="maturityMode" size="small" class="mb4">
              <el-radio-button value="tenor">стандартный срок</el-radio-button>
              <el-radio-button value="date">дата</el-radio-button>
            </el-radio-group>
            <el-select v-if="maturityMode === 'tenor'" v-model="req.tenor" style="width: 120px">
              <el-option v-for="t in tenors" :key="t" :value="t" />
            </el-select>
            <el-date-picker v-else v-model="req.maturity" type="date" value-format="YYYY-MM-DD" />
          </el-form-item>
          <div class="two">
            <el-form-item label="Купон, б.п.">
              <el-input-number v-model="req.coupon_bp" :min="0" :max="10000" :step="5" controls-position="right" />
            </el-form-item>
            <el-form-item label="Номинал, ₽">
              <el-input-number v-model="req.notional" :min="1" :step="10000000" controls-position="right" />
            </el-form-item>
            <el-form-item label="Сторона">
              <el-radio-group v-model="req.side">
                <el-radio-button value="buyer">покупатель</el-radio-button>
                <el-radio-button value="seller">продавец</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item v-if="req.profile === 'SPFI_STD'">
              <template #label>
                <el-tooltip content="Цена сделки или заявки на СПФИ (PUF, % номинала, плюс — платит покупатель). Пусто — NPV по модели без upfront"><span>Upfront (PUF), % <el-icon><QuestionFilled /></el-icon></span></el-tooltip>
              </template>
              <el-input-number v-model="req.upfront_pct" :step="0.01" :precision="4" controls-position="right" placeholder="нет" />
            </el-form-item>
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
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { formatDate } from '@cds/platform'

import { api } from '../api'
import CdsScheduleTable from '../components/CdsScheduleTable.vue'
import { num } from '../format'
import type { CdsContractRequest, CdsContractResult, CdsEntity } from '../types'

const route = useRoute()
const entities = ref<CdsEntity[]>([])
const tenors = ref<string[]>(['6M', '1Y', '2Y', '3Y', '4Y', '5Y'])
const maturityMode = ref<'tenor' | 'date'>('tenor')
const req = ref<CdsContractRequest>({ entity_code: '', profile: 'SPFI_STD', side: 'buyer', tenor: '5Y', coupon_bp: 100, notional: 100_000_000, upfront_pct: null })
const result = ref<CdsContractResult | null>(null)
const error = ref('')
const calculating = ref(false)
const exporting = ref(false)

const recoveryPct = computed({
  get: () => (req.value.recovery == null ? undefined : Math.round(req.value.recovery * 1000) / 10),
  set: (v: number | undefined | null) => {
    req.value.recovery = v == null ? null : v / 100
  },
})
const money = (v: number) => v.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

function body(): CdsContractRequest {
  return {
    ...req.value,
    tenor: maturityMode.value === 'tenor' ? req.value.tenor : null,
    maturity: maturityMode.value === 'date' ? req.value.maturity : null,
    upfront_pct: req.value.profile === 'SPFI_STD' ? req.value.upfront_pct : null,
    as_of_date: req.value.as_of_date || null,
    trade_date: req.value.trade_date || null,
  }
}

function onEntity() {
  const e = entities.value.find((x) => x.code === req.value.entity_code)
  if (e && req.value.profile === 'SPFI_STD') req.value.coupon_bp = e.standard_coupon_bp
}

function onProfile() {
  if (req.value.profile === 'SBER_OTC') {
    maturityMode.value = 'date'
    req.value.upfront_pct = null
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
  grid-template-columns: 380px minmax(0, 1fr);
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
.mb4 {
  margin-bottom: 6px;
  display: block;
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
