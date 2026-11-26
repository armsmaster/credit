<template>
  <div v-loading="loading">
    <div class="page-toolbar">
      <el-button text @click="back"><el-icon><ArrowLeft /></el-icon></el-button>
      <h2>{{ tradeId ? `Сделка ${form.external_id || ''} — изменение` : 'Новая сделка CDS' }}</h2>
      <el-tag v-if="tradeId && version" size="small" type="info">сейчас версия {{ version }}, сохранится {{ version + 1 }}</el-tag>
      <div class="spacer" />
      <el-button @click="back">Отмена</el-button>
      <el-button type="primary" :loading="saving" :disabled="!canSave" @click="save">Сохранить</el-button>
    </div>
    <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon class="mb" />

    <div class="layout">
      <el-card shadow="never">
        <el-form label-position="top" size="default">
          <div class="two">
            <el-form-item label="External ID" required>
              <el-input v-model="form.external_id" maxlength="100" placeholder="номер во внешней системе" />
            </el-form-item>
            <el-form-item label="Контрольное лицо" required>
              <el-select v-model="form.entity_code" filterable placeholder="эмитент" style="width: 100%">
                <el-option v-for="e in meta?.entities ?? []" :key="e.code" :value="e.code" :label="`${e.code} — ${e.name}`" />
              </el-select>
            </el-form-item>
          </div>
          <el-form-item label="Сторона">
            <el-radio-group v-model="form.side">
              <el-radio-button value="buyer">покупатель защиты</el-radio-button>
              <el-radio-button value="seller">продавец защиты</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <div class="two">
            <el-form-item label="Номинал, ₽" required><AmountInput v-model="form.notional" /></el-form-item>
            <el-form-item label="Купон, б.п.">
              <div class="coupon">
                <el-radio-group v-model="couponMode" size="small">
                  <el-radio-button value="std">100</el-radio-button>
                  <el-radio-button value="custom">свой</el-radio-button>
                </el-radio-group>
                <el-input-number v-if="couponMode === 'custom'" v-model="customCoupon" :min="0.01" :max="10000" :step="5" :precision="2"
                  controls-position="right" style="width: 110px" />
              </div>
            </el-form-item>
            <el-form-item label="Дата сделки" required>
              <el-date-picker v-model="form.trade_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
            <el-form-item label="Дата экспирации" required>
              <el-date-picker v-model="form.maturity" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
          </div>
          <el-form-item>
            <template #label>
              <span>Ребейт</span>
            </template>
            <div class="rebate">
              <el-switch v-model="form.rebate" />
              <span class="hint">{{ form.rebate
                ? 'Первый купон — за весь купонный период; накопленное с начала периода до даты начала срока возвращает продавец'
                : 'Первый купон — за период от даты начала срока до первой даты купона' }}</span>
            </div>
          </el-form-item>
          <el-form-item label="Дата начала срока">
            <el-date-picker v-model="form.effective_date" type="date" value-format="YYYY-MM-DD" clearable style="width: 100%"
              :placeholder="form.rebate ? 'авто: T + 1 рабочий день' : 'авто: дата сделки'" />
          </el-form-item>

          <div class="section">Расписание</div>
          <el-form-item>
            <el-radio-group v-model="params.mode">
              <el-radio-button value="standard">стандартные даты CDS</el-radio-button>
              <el-radio-button value="custom">своё расписание</el-radio-button>
            </el-radio-group>
            <div class="hint">{{ params.mode === 'standard'
              ? 'Купоны 20 марта, июня, сентября и декабря; перенос «Следующий», даты периодов сдвигаются вместе с платежами'
              : 'Генератор как у процентного свопа: частота, направление, неполный период, день ролла, перенос дат' }}</div>
          </el-form-item>
          <div v-if="params.mode === 'custom'" class="two">
            <el-form-item label="Частота">
              <el-select v-model="params.frequency_months">
                <el-option :value="1" label="ежемесячно (1M)" />
                <el-option :value="3" label="ежеквартально (3M)" />
                <el-option :value="6" label="раз в полгода (6M)" />
                <el-option :value="12" label="раз в год (12M)" />
              </el-select>
            </el-form-item>
            <el-form-item label="День ролла">
              <el-input-number v-model="params.roll_day" :min="1" :max="31" controls-position="right" placeholder="авто" style="width: 100%" />
            </el-form-item>
            <el-form-item label="Генерация дат">
              <el-select v-model="params.direction">
                <el-option value="backward" label="назад от экспирации" />
                <el-option value="forward" label="вперёд от начала срока" />
              </el-select>
            </el-form-item>
            <el-form-item label="Неполный период">
              <el-select v-model="params.stub">
                <el-option value="short" label="короткий" />
                <el-option value="long" label="длинный" />
              </el-select>
            </el-form-item>
            <el-form-item label="Перенос дат">
              <el-select v-model="params.bdc">
                <el-option value="following" label="Следующий (Following)" />
                <el-option value="modified_following" label="Мод. следующий (Mod. Following)" />
                <el-option value="preceding" label="Предыдущий (Preceding)" />
                <el-option value="unadjusted" label="Без переноса" />
              </el-select>
            </el-form-item>
            <el-form-item label=" ">
              <el-checkbox v-model="params.adjust_accrual">даты периодов — как даты платежей</el-checkbox>
            </el-form-item>
          </div>
          <div class="two">
            <el-form-item label="Календарь">
              <el-select v-model="params.calendar">
                <el-option v-for="c in meta?.calendars ?? []" :key="c.code" :value="c.code" :label="c.name" />
              </el-select>
            </el-form-item>
            <el-form-item label="Коэффициент дней">
              <el-select v-model="params.day_count">
                <el-option value="ACT/ACT.ISDA" label="ACT/ACT ISDA" />
                <el-option value="ACT/365F" label="ACT/365F" />
                <el-option value="ACT/360" label="ACT/360" />
              </el-select>
            </el-form-item>
          </div>
          <el-checkbox v-model="params.last_period_inclusive" class="mb">последний период включает дату экспирации</el-checkbox>

          <div class="section">Поиск</div>
          <el-form-item label="Теги">
            <el-select v-model="form.tags" multiple filterable allow-create default-first-option remote :remote-method="loadTags"
              :loading="tagsLoading" :reserve-keyword="false" placeholder="начните вводить: подсказки из базы, Enter — новый тег"
              style="width: 100%" @focus="loadTags('')">
              <el-option v-for="t in tagOptions" :key="t.name" :value="t.name" :label="t.name">
                <span>{{ t.name }}</span><span class="tag-count">{{ t.trades }}</span>
              </el-option>
            </el-select>
          </el-form-item>
          <el-form-item label="Комментарий">
            <el-input v-model="form.comment" type="textarea" :rows="2" maxlength="1000" />
          </el-form-item>
        </el-form>
      </el-card>

      <el-card shadow="never" class="sched-card">
        <template #header>
          <div class="card-head">
            <b>График платежей</b>
            <el-tag v-if="edited" size="small" type="warning">есть ручные правки</el-tag>
            <div class="spacer" />
            <el-button size="small" :disabled="!termsReady" :loading="previewing" @click="regenerate">
              <el-icon><Refresh /></el-icon>&nbsp;Сформировать заново
            </el-button>
          </div>
        </template>
        <el-alert v-if="termsChangedWithEdits" type="warning" :closable="false" show-icon class="mb"
          title="Условия изменились, а в графике есть ручные правки: даты не перегенерированы. Нажмите «Сформировать заново», чтобы построить график по новым условиям." />
        <el-alert v-if="scheduleError" type="error" :title="scheduleError" :closable="false" class="mb" />
        <div v-if="schedule" class="facts">
          <span>Начало срока <b>{{ formatDate(schedule.effective_date) }}</b></span>
          <span>Начало первого периода <b>{{ formatDate(rows[0]?.accrual_start) }}</b></span>
          <span v-if="schedule.rebate_start">
            Ребейт {{ formatDate(schedule.rebate_start) }} → {{ formatDate(schedule.rebate_end) }} (искл.):
            <b>{{ money(schedule.rebate_amount) }} ₽</b> (платит продавец)
          </span>
          <span v-else class="muted">ребейта нет</span>
          <span>Купоны всего <b>{{ money(totalAmount) }} ₽</b></span>
        </div>
        <el-empty v-if="!rows.length" :description="termsReady ? 'Нет периодов' : 'Заполните номинал, купон и даты — график построится сам'" />
        <el-table v-else :data="rows" size="small" border :row-class-name="rowClass">
          <el-table-column type="index" label="№" width="44" />
          <el-table-column label="Начало" min-width="130">
            <template #default="{ row, $index }">
              <el-date-picker v-if="$index === 0" v-model="row.accrual_start" type="date" value-format="YYYY-MM-DD" size="small"
                :clearable="false" style="width: 120px" @change="cellChanged" />
              <span v-else>{{ formatDate(row.accrual_start) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="Конец" min-width="130">
            <template #default="{ row, $index }">
              <el-date-picker v-if="$index < rows.length - 1" v-model="row.accrual_end" type="date" value-format="YYYY-MM-DD" size="small"
                :clearable="false" style="width: 120px" @change="endChanged($index)" />
              <span v-else>{{ formatDate(row.accrual_end) }}<span v-if="params.last_period_inclusive" class="muted"> (вкл.)</span></span>
            </template>
          </el-table-column>
          <el-table-column label="Платёж" min-width="130">
            <template #default="{ row }">
              <el-date-picker v-model="row.pay_date" type="date" value-format="YYYY-MM-DD" size="small" :clearable="false" style="width: 120px"
                @change="cellChanged" />
            </template>
          </el-table-column>
          <el-table-column label="Дней" prop="days" width="60" align="right" />
          <el-table-column label="α" min-width="86" align="right"><template #default="{ row }">{{ num(row.year_frac ?? 0, 6) }}</template></el-table-column>
          <el-table-column label="Купон, ₽" min-width="150" align="right">
            <template #default="{ row }">
              <div class="amount-cell">
                <el-input-number v-model="row.amount" :precision="2" :step="1000" :controls="false" size="small" style="width: 120px"
                  @change="row.manual = true; cellChanged()" />
                <el-tooltip v-if="row.manual" content="Сумма введена вручную — вернуть расчётную по датам">
                  <el-button link size="small" @click="row.manual = false; cellChanged()"><el-icon><RefreshLeft /></el-icon></el-button>
                </el-tooltip>
              </div>
            </template>
          </el-table-column>
          <el-table-column width="44" align="center">
            <template #default="{ $index }">
              <el-tooltip v-if="$index < rows.length - 1" content="Объединить с следующим периодом">
                <el-button link size="small" type="danger" @click="mergeNext($index)"><el-icon><Delete /></el-icon></el-button>
              </el-tooltip>
            </template>
          </el-table-column>
        </el-table>
        <div class="hint mt">
          Можно поправить начало первого периода, концы периодов (начало следующего сдвигается вместе с ними), даты платежей и суммы
          купонов. Суммы следуют за датами, пока не введены вручную (подсвечены). Удаление объединяет период со следующим.
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { formatDate } from '@cds/platform'

import { api } from '../api'
import AmountInput from '../components/AmountInput.vue'
import { num } from '../format'
import type { TradeInput, TradeRow, TradeSchedule, TradeScheduleParams, TradesMeta, TradeTerms } from '../types'

const route = useRoute()
const router = useRouter()
const tradeId = computed(() => (route.params.id as string) || '')
const meta = ref<TradesMeta | null>(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const version = ref(0)

const form = reactive({
  external_id: '',
  entity_code: '',
  side: 'buyer' as 'buyer' | 'seller',
  notional: 100_000_000 as number | null,
  trade_date: null as string | null,
  maturity: null as string | null,
  effective_date: null as string | null,
  rebate: true,
  tags: [] as string[],
  comment: '',
})
const couponMode = ref<'std' | 'custom'>('std')
const customCoupon = ref<number>(500)
const coupon = computed(() => (couponMode.value === 'std' ? 100 : customCoupon.value))
const params = reactive<TradeScheduleParams>({
  mode: 'standard',
  frequency_months: 3,
  direction: 'backward',
  stub: 'short',
  roll_day: null,
  bdc: 'following',
  adjust_accrual: true,
  calendar: null,
  day_count: 'ACT/ACT.ISDA',
  last_period_inclusive: true,
})

const schedule = ref<TradeSchedule | null>(null)
const rows = ref<TradeRow[]>([])
const edited = ref(false)
const scheduleError = ref('')
const previewing = ref(false)
const termsChangedWithEdits = ref(false)
const tagOptions = ref<{ name: string; trades: number }[]>([])
const tagsLoading = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let seq = 0
let hydrating = false

const money = (v: number) => v.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const totalAmount = computed(() => rows.value.reduce((a, r) => a + (r.amount ?? 0), 0))
const termsReady = computed(() => !!(form.notional && coupon.value && form.trade_date && form.maturity && form.maturity > form.trade_date))
const canSave = computed(() => termsReady.value && !!form.external_id.trim() && !!form.entity_code && rows.value.length > 0 && !scheduleError.value)

function terms(withRows: boolean): TradeTerms {
  return {
    side: form.side,
    notional: form.notional,
    coupon_bp: coupon.value,
    trade_date: form.trade_date,
    effective_date: form.effective_date || null,
    maturity: form.maturity,
    rebate: form.rebate,
    schedule_params: { ...params },
    rows: withRows ? rows.value.map((r) => ({ ...r, amount: r.manual ? r.amount : null })) : null,
  }
}

function rowClass({ row }: { row: TradeRow }) {
  return row.manual ? 'manual-row' : ''
}

async function preview(withRows: boolean) {
  if (!termsReady.value) return
  const my = ++seq
  previewing.value = true
  try {
    const s = await api.trades.schedule(terms(withRows))
    if (my !== seq) return
    schedule.value = s
    rows.value = s.rows.map((r) => ({ ...r }))
    edited.value = s.edited
    scheduleError.value = ''
    if (!withRows) termsChangedWithEdits.value = false
  } catch (e: any) {
    if (my !== seq) return
    scheduleError.value = e.message
  } finally {
    if (my === seq) previewing.value = false
  }
}

function schedulePreview(withRows: boolean, delay = 300) {
  clearTimeout(timer)
  timer = setTimeout(() => preview(withRows), delay)
}

function regenerate() {
  termsChangedWithEdits.value = false
  preview(false)
}

function cellChanged() {
  schedulePreview(true, 150)
}

function endChanged(i: number) {
  if (rows.value[i + 1]) rows.value[i + 1].accrual_start = rows.value[i].accrual_end
  cellChanged()
}

function mergeNext(i: number) {
  const r = rows.value
  r[i + 1] = { ...r[i + 1], accrual_start: r[i].accrual_start, manual: false }
  r.splice(i, 1)
  cellChanged()
}

// terms that define the dates: regenerate unless the schedule has hand edits
watch(
  () => [form.trade_date, form.maturity, form.effective_date, form.rebate, JSON.stringify(params)],
  () => {
    if (hydrating) return
    if (edited.value) {
      termsChangedWithEdits.value = true
      schedulePreview(true) // amounts and validation on the edited dates
    } else schedulePreview(false)
  },
)
// notional and coupon change amounts only
watch(
  () => [form.notional, coupon.value],
  () => {
    if (!hydrating) schedulePreview(edited.value)
  },
)
watch(
  () => form.entity_code,
  (code) => {
    if (hydrating || tradeId.value) return
    const e = meta.value?.entities.find((x) => x.code === code)
    if (e && e.standard_coupon_bp !== 100) {
      couponMode.value = 'custom'
      customCoupon.value = e.standard_coupon_bp
    }
  },
)

async function loadTags(q: string) {
  tagsLoading.value = true
  try {
    tagOptions.value = await api.trades.tags(q || undefined)
  } catch {
    tagOptions.value = []
  } finally {
    tagsLoading.value = false
  }
}

async function save() {
  saving.value = true
  error.value = ''
  try {
    const body: TradeInput = { ...terms(edited.value), external_id: form.external_id.trim(), entity_code: form.entity_code, tags: form.tags, comment: form.comment }
    const t = tradeId.value ? await api.trades.update(tradeId.value, body) : await api.trades.create(body)
    ElMessage.success(tradeId.value ? `Сохранена версия ${t.version}` : 'Сделка сохранена')
    router.push(`/credit/cds/trades/${t.id}`)
  } catch (e: any) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

function back() {
  if (tradeId.value) router.push(`/credit/cds/trades/${tradeId.value}`)
  else router.push('/credit/cds/trades')
}

onMounted(async () => {
  loading.value = true
  hydrating = true
  try {
    meta.value = await api.trades.meta()
    params.calendar = meta.value.defaults.calendar
    params.day_count = meta.value.defaults.day_count
    if (tradeId.value) {
      const t = await api.trades.get(tradeId.value)
      version.value = t.version
      Object.assign(form, {
        external_id: t.external_id, entity_code: t.entity_code, side: t.side, notional: t.notional, trade_date: t.trade_date,
        maturity: t.maturity, effective_date: t.effective_date, rebate: t.rebate, tags: [...t.tags], comment: t.comment,
      })
      if (t.coupon_bp === 100) couponMode.value = 'std'
      else {
        couponMode.value = 'custom'
        customCoupon.value = t.coupon_bp
      }
      Object.assign(params, t.schedule_params)
      rows.value = t.schedule.rows.map((r) => ({ ...r }))
      edited.value = t.schedule_edited
      schedule.value = { ...t.schedule, rows: rows.value, edited: t.schedule_edited, effective_date: t.effective_date,
        first_period_start: rows.value[0]?.accrual_start ?? null, schedule_params: t.schedule_params }
    } else {
      form.trade_date = meta.value.today
      if (route.query.entity) form.entity_code = String(route.query.entity)
    }
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
    setTimeout(() => (hydrating = false), 0)
  }
})
onBeforeUnmount(() => clearTimeout(timer))
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
.spacer {
  flex: 1;
}
.layout {
  display: grid;
  grid-template-columns: 480px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}
.two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 12px;
}
.coupon,
.rebate {
  display: flex;
  align-items: center;
  gap: 10px;
}
.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.35;
}
.rebate .hint {
  flex: 1;
}
.section {
  font-weight: 600;
  margin: 6px 0 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--el-border-color);
}
.card-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.facts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
  font-size: 13px;
  margin-bottom: 8px;
}
.muted {
  color: var(--el-text-color-secondary);
}
.amount-cell {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;
}
.amount-cell :deep(.el-input__inner) {
  text-align: right;
}
:deep(.manual-row) td {
  background: var(--el-color-warning-light-9) !important;
}
.tag-count {
  float: right;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.mb {
  margin-bottom: 12px;
}
.mt {
  margin-top: 8px;
}
</style>
