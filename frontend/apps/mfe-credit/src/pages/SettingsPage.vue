<template>
  <div v-loading="loading" class="settings">
    <div class="page-toolbar">
      <h2>Настройки кредитных кривых</h2>
      <div class="spacer" />
      <el-button v-if="isOperator" type="primary" :loading="saving" @click="save">Сохранить</el-button>
    </div>
    <template v-if="form">
      <div class="grid">
        <el-card shadow="never">
          <template #header><b>Кривые для плавающих индексов</b></template>
          <p class="muted">Прогноз плавающих купонов строится по форвардам соответствующей процентной кривой; индексация номинала линкеров ВЭБ.РФ — по кривой RUONIA.</p>
          <el-form label-position="top" :disabled="!isOperator">
            <el-form-item label="Ключевая ставка ЦБ (бенчмарк ISS RREFKEYR)">
              <el-select v-model="form.index_curves.KEYRATE" filterable style="width: 100%">
                <el-option v-for="c in rateCurves" :key="c.code" :value="c.code" :label="`${c.code} — ${c.name}`" />
              </el-select>
            </el-form-item>
            <el-form-item label="RUONIA (бенчмарки RUONIA, RUONIA_IND; номинал линкеров)">
              <el-select v-model="form.index_curves.RUONIA" filterable style="width: 100%">
                <el-option v-for="c in rateCurves" :key="c.code" :value="c.code" :label="`${c.code} — ${c.name}`" />
              </el-select>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card shadow="never">
          <template #header><b>Ликвидность и рекомендации</b></template>
          <el-form label-position="top" :disabled="!isOperator">
            <el-form-item label="Окно статистики торгов, календарных дней">
              <el-input-number v-model="form.liquidity.window_days" :min="10" :max="730" controls-position="right" />
            </el-form-item>
            <el-form-item label="Рекомендовать при среднем обороте не ниже, ₽/день">
              <el-input-number v-model="form.liquidity.min_avg_turnover" :min="0" :step="500000" controls-position="right" style="width: 220px" />
            </el-form-item>
            <el-form-item label="и среднем числе сделок не ниже, в день">
              <el-input-number v-model="form.liquidity.min_avg_trades" :min="0" :step="1" controls-position="right" />
            </el-form-item>
          </el-form>
          <p class="muted small">Новое окно применяется со следующего скана эмитентов (ежедневно в 06:15 или кнопкой «Сканировать»).</p>
        </el-card>

        <el-card shadow="never">
          <template #header><b>DEMA часовых цен закрытия</b></template>
          <el-form label-position="top" :disabled="!isOperator">
            <el-form-item label="Период полураспада">
              <el-radio-group v-model="demaMode">
                <el-radio value="auto">Подбирать автоматически</el-radio>
                <el-radio value="fixed">Задать вручную</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item v-if="demaMode === 'fixed'" label="Период полураспада, торговых часов">
              <el-input-number v-model="halfLife" :min="0.5" :max="1000" :step="1" controls-position="right" />
            </el-form-item>
            <el-form-item label="Глубина свечей, дней">
              <el-input-number v-model="form.dema.lookback_days" :min="10" :max="365" controls-position="right" />
            </el-form-item>
          </el-form>
          <div v-if="auto" class="auto">
            <div>
              Автоподбор: общий период полураспада <b>{{ num(auto.half_life_hours, 1) }}</b> торг. ч
              <span class="muted">(рассчитан {{ formatDateTime(auto.computed_at) }})</span>
            </div>
            <p class="muted small">
              Выбирается период, минимизирующий ошибку прогноза следующей часовой цены закрытия: слишком быстрая DEMA повторяет
              шум (ошибки отрицательно автокоррелированы — «возврат к среднему»), слишком медленная — запаздывает (ошибки
              положительно автокоррелированы). Бумагам с достаточным числом свечей период подбирается индивидуально, остальным —
              общий. Подробнее — в docs/credit-math.md.
            </p>
            <el-table :data="autoRows" size="small" max-height="260">
              <el-table-column prop="secid" label="Бумага" />
              <el-table-column label="Период, ч" align="right"><template #default="{ row }">{{ num(row.half_life, 1) }}</template></el-table-column>
              <el-table-column label="Свечей" prop="candles" align="right" />
              <el-table-column label="Автокорр. ошибок" align="right"><template #default="{ row }">{{ num(row.acf1, 2) }}</template></el-table-column>
              <el-table-column label=""><template #default="{ row }"><el-tag v-if="row.pooled" size="small" type="info">общий</el-tag></template></el-table-column>
            </el-table>
          </div>
        </el-card>

        <el-card v-if="cds" shadow="never">
          <template #header>
            <div class="card-head"><b>Прайсинг CDS</b><div class="spacer" />
              <el-button v-if="isOperator" size="small" type="primary" :loading="savingCds" @click="saveCds">Сохранить</el-button>
            </div>
          </template>
          <p class="muted small">Открытые вопросы к НКЦ и Сберу из спецификации заведены как настройки: ответ меняет параметр, а не код. После изменения CDS-кривые пересчитываются при следующем сохранении кривых или кнопкой «Пересчитать».</p>
          <el-form label-position="top" :disabled="!isOperator" size="small">
            <div class="two">
              <el-form-item label="Rq для конверсии QS ↔ PUF, %">
                <el-input-number v-model="rqPct" :min="0" :max="95" :step="5" controls-position="right" />
              </el-form-item>
              <el-form-item label="Коэффициент дней купона и ребейта">
                <el-select v-model="cds.day_count" style="width: 100%">
                  <el-option value="ACT/ACT.ISDA" label="ACT/ACT ISDA (СПФИ)" />
                  <el-option value="ACT/360" label="ACT/360 (междунар.)" />
                  <el-option value="ACT/365F" label="ACT/365F" />
                </el-select>
              </el-form-item>
            </div>
            <el-form-item label="Стандартные сроки">
              <el-checkbox-group v-model="cds.tenors">
                <el-checkbox v-for="t in ['6M', '1Y', '2Y', '3Y', '4Y', '5Y', '7Y', '10Y']" :key="t" :value="t">{{ t }}</el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-form-item label="Ребейт (пример Мосбиржи 28.10.2026: 38 или 39 дней; конец — Дата начала срока, искл.)">
              <el-select v-model="cds.rebate_mode" style="width: 100%">
                <el-option value="spec" label="По спецификации: от смещённой CDS-даты (38 дн.)" />
                <el-option value="unadjusted_start" label="От несмещённой CDS-даты (39 дн., слайд)" />
                <el-option value="inclusive_end" label="С включением Даты начала срока (39 дн.)" />
              </el-select>
            </el-form-item>
            <el-checkbox v-model="cds.protection_from_effective" class="wrap-check">Защита с Даты начала срока (иначе ISDA: step-in T+1 календарный день)</el-checkbox>
            <el-checkbox v-model="cds.sber_adjust_period_ends" class="wrap-check">Сбер: концы периодов сдвигаются по «Следующий» вместе с платежами</el-checkbox>
            <el-checkbox v-model="cds.sber_last_period_inclusive" class="wrap-check">Сбер: последний период включает Плановую дату прекращения</el-checkbox>
            <div class="two mt">
              <el-form-item label="Календарь СПФИ">
                <el-select v-model="cds.spfi_calendar" style="width: 100%"><el-option v-for="c in calendars" :key="c.code" :value="c.code" :label="c.name" /></el-select>
              </el-form-item>
              <el-form-item label="Календарь Сбера">
                <el-select v-model="cds.sber_calendar" style="width: 100%"><el-option v-for="c in calendars" :key="c.code" :value="c.code" :label="c.name" /></el-select>
              </el-form-item>
            </div>
          </el-form>
        </el-card>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'

import { formatDateTime, getPlatform, Role } from '@cds/platform'

import { api } from '../api'
import { num } from '../format'
import type { CalendarInfo, CdsSettings, RateCurveDef, Settings } from '../types'

const isOperator = getPlatform().hasRole(Role.Operator)
const loading = ref(false)
const saving = ref(false)
const form = ref<Settings | null>(null)
const rateCurves = ref<RateCurveDef[]>([])
const demaMode = ref<'auto' | 'fixed'>('auto')
const halfLife = ref<number>(8)

const cds = ref<CdsSettings | null>(null)
const calendars = ref<CalendarInfo[]>([])
const savingCds = ref(false)
const rqPct = computed({
  get: () => (cds.value ? Math.round(cds.value.recovery_quote * 1000) / 10 : 40),
  set: (v: number | undefined) => {
    if (cds.value) cds.value.recovery_quote = (v ?? 40) / 100
  },
})

async function saveCds() {
  if (!cds.value) return
  savingCds.value = true
  try {
    cds.value = await api.cds.saveSettings(cds.value)
    ElMessage.success('Настройки CDS сохранены')
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    savingCds.value = false
  }
}

const auto = computed(() => form.value?.dema_auto ?? null)
const autoRows = computed(() => Object.entries(auto.value?.bonds ?? {}).map(([secid, v]) => ({ secid, ...v })))

async function load() {
  loading.value = true
  try {
    const [s, curves, cdsMeta] = await Promise.all([api.settings.get(), api.rateCurves().catch(() => []), api.cds.meta()])
    form.value = s
    cds.value = cdsMeta.settings
    calendars.value = cdsMeta.calendars
    rateCurves.value = curves
    demaMode.value = s.dema.half_life_hours ? 'fixed' : 'auto'
    halfLife.value = s.dema.half_life_hours ?? s.dema_auto?.half_life_hours ?? 8
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!form.value) return
  saving.value = true
  try {
    form.value = await api.settings.save({
      index_curves: form.value.index_curves,
      liquidity: form.value.liquidity,
      dema: { half_life_hours: demaMode.value === 'fixed' ? halfLife.value : null, lookback_days: form.value.dema.lookback_days },
    })
    ElMessage.success('Настройки сохранены')
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

onMounted(load)
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
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 12px;
  align-items: start;
}
.muted {
  color: var(--el-text-color-secondary);
}
.small {
  font-size: 12px;
}
.auto {
  margin-top: 8px;
}
.card-head {
  display: flex;
  align-items: center;
}
.two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 12px;
}
.mt {
  margin-top: 8px;
}
.wrap-check {
  display: flex;
  align-items: flex-start;
  height: auto;
  margin: 0 0 8px;
  white-space: normal;
}
.wrap-check :deep(.el-checkbox__input) {
  margin-top: 2px;
}
.wrap-check :deep(.el-checkbox__label) {
  white-space: normal;
  line-height: 1.4;
}
</style>
