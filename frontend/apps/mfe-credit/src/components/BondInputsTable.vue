<template>
  <el-table :data="rows" size="small" border class="inputs" :row-class-name="rowClass">
    <el-table-column v-if="editable" width="40" align="center" fixed>
      <template #default="{ row }">
        <el-checkbox v-model="row.enabled" @change="changed" />
      </template>
    </el-table-column>
    <el-table-column label="Бумага" min-width="170" fixed>
      <template #default="{ row }">
        <a href="#" class="bond-link" @click.prevent="emit('open', row.secid)">{{ row.shortname || row.secid }}</a>
        <el-tooltip v-if="row.problems?.length" placement="top">
          <template #content><div v-for="p in row.problems" :key="p">{{ p }}</div></template>
          <el-icon color="var(--el-color-danger)" class="ico"><WarningFilled /></el-icon>
        </el-tooltip>
        <div class="muted small">
          {{ KIND[row.kind ?? ''] ?? '' }}<template v-if="row.coupon_index"> · {{ row.coupon_index === 'KEYRATE' ? 'КС' : 'RUONIA' }}+{{ row.spread_pct }}%</template>
        </div>
      </template>
    </el-table-column>
    <el-table-column label="Погашение" width="110">
      <template #default="{ row }">
        <span :class="{ warn: row.short }">{{ formatDate(row.maturity) }}</span>
        <div class="muted small">{{ num(row.years, 2) }} г.<template v-if="row.to_offer"> · оферта</template></div>
        <el-tag v-if="row.short" type="warning" size="small">исключить?</el-tag>
      </template>
    </el-table-column>
    <el-table-column v-if="editable" label="Вес" width="96">
      <template #default="{ row }">
        <el-input-number v-model="row.weight" :min="0.01" :max="100" :step="0.5" size="small" controls-position="right" style="width: 80px" @change="changed" />
      </template>
    </el-table-column>
    <el-table-column v-for="k in OBS_KINDS" :key="k" :label="OBS_TITLES[k]" width="84" align="right">
      <template #header>
        <el-tooltip :content="k === 'last' || k === 'vwap' ? 'Приоритет 2' : 'Приоритет 1'"><span>{{ OBS_TITLES[k] }}</span></el-tooltip>
      </template>
      <template #default="{ row }">
        <div
          v-if="row.market.prices[k] != null"
          class="obs"
          :class="{ off: row.excluded_obs.includes(k), clickable: editable }"
          :title="editable ? (row.excluded_obs.includes(k) ? 'Включить в расчёт' : 'Исключить из расчёта') : ''"
          @click="editable && toggleObs(row, k)"
        >
          {{ num(row.market.prices[k], k === 'dema' ? 3 : 2) }}
          <div v-if="errors[row.secid]?.[k] != null" class="err" :class="errClass(errors[row.secid][k] ?? 0)">{{ signed(errors[row.secid][k], 0) }}</div>
        </div>
        <span v-else class="muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="Ручная цена" width="110" align="right">
      <template #default="{ row }">
        <el-input-number v-if="editable" v-model="row.manual_price" :min="1" :max="1000" :step="0.05" :precision="3" size="small" :controls="false" style="width: 90px" placeholder="—" @change="changed" />
        <span v-else>{{ num(row.manual_price, 3) }}</span>
      </template>
    </el-table-column>
    <el-table-column label="Модель" width="90" align="right">
      <template #header><el-tooltip content="Модельная чистая цена, % номинала"><span>Модель</span></el-tooltip></template>
      <template #default="{ row }">
        <b v-if="fit[row.secid]?.model_price != null">{{ num(fit[row.secid].model_price, 3) }}</b>
        <span v-else class="muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="Ошибка, б.п." width="88" align="right">
      <template #header><el-tooltip content="Модельная цена минус взвешенная цена облака, в б.п. спреда (через DV01)"><span>Ошибка, б.п.</span></el-tooltip></template>
      <template #default="{ row }">
        <span v-if="fit[row.secid]?.error_bp != null" :class="errClass(fit[row.secid].error_bp!)">{{ signed(fit[row.secid].error_bp, 1) }}</span>
      </template>
    </el-table-column>
    <el-table-column label="Z-спред рын./мод." width="110" align="right">
      <template #default="{ row }">
        <template v-if="fit[row.secid]?.z_spread_market_bp != null">
          {{ num(fit[row.secid].z_spread_market_bp, 0) }} / {{ num(fit[row.secid].z_spread_model_bp, 0) }}
        </template>
      </template>
    </el-table-column>
    <el-table-column label="Данные" min-width="130">
      <template #default="{ row }">
        <el-tag size="small" :type="SOURCE[row.market.source]?.type ?? 'info'">{{ SOURCE[row.market.source]?.label ?? row.market.source }}</el-tag>
        <div class="muted small">{{ time(row.market.as_of) }}</div>
        <div v-if="row.market.dema_half_life" class="muted small">DEMA h={{ num(row.market.dema_half_life, 1) }} ч</div>
        <div v-if="row.status && row.status !== 'used'" class="muted small">{{ STATUS[row.status] ?? row.status }}</div>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { formatDate } from '@cds/platform'

import { num, OBS_KINDS, OBS_TITLES, signed } from '../format'
import type { CalcBond, CalcResult, Fitted, ObsKind } from '../types'

const props = withDefaults(defineProps<{ rows: CalcBond[]; result: CalcResult | null; editable?: boolean }>(), { editable: false })
const emit = defineEmits<{ change: []; open: [string] }>()

const KIND: Record<string, string> = { fixed: 'фикс', floating: 'флоатер', linker: 'индекс. номинал' }
const SOURCE: Record<string, { label: string; type: 'success' | 'info' | 'warning' | 'danger' }> = {
  intraday: { label: 'внутри дня', type: 'success' },
  candles: { label: 'только свечи', type: 'warning' },
  eod: { label: 'на 19:00', type: 'info' },
  none: { label: 'нет данных', type: 'danger' },
}
const STATUS: Record<string, string> = { skipped: 'пропущена', excluded: 'выключена', no_prices: 'нет цен' }

const fit = computed<Record<string, Fitted>>(() => Object.fromEntries((props.result?.fitted ?? []).map((f) => [f.secid, f])))
const errors = computed<Record<string, Record<string, number | null>>>(() =>
  Object.fromEntries((props.result?.fitted ?? []).map((f) => [f.secid, Object.fromEntries(f.observations.map((o) => [o.kind, o.error_bp]))])),
)

function toggleObs(row: CalcBond, k: ObsKind) {
  row.excluded_obs = row.excluded_obs.includes(k) ? row.excluded_obs.filter((x) => x !== k) : [...row.excluded_obs, k]
  changed()
}

function changed() {
  emit('change')
}

const errClass = (v: number) => (Math.abs(v) >= 50 ? 'bad' : Math.abs(v) >= 15 ? 'mid' : 'good')
const time = (v: string | null) => (v ? (v.length > 10 ? new Date(v).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : formatDate(v)) : '')
function rowClass({ row }: { row: CalcBond }) {
  return !row.enabled ? 'row-off' : row.status === 'skipped' ? 'row-skipped' : ''
}
</script>

<style scoped>
.muted {
  color: var(--el-text-color-secondary);
}
.small {
  font-size: 11px;
}
.bond-link {
  font-weight: 600;
  text-decoration: none;
  color: var(--el-color-primary);
}
.ico {
  vertical-align: middle;
  margin-left: 4px;
}
.warn {
  color: var(--el-color-warning-dark-2);
  font-weight: 600;
}
.obs {
  line-height: 1.2;
}
.obs.clickable {
  cursor: pointer;
}
.obs.off {
  text-decoration: line-through;
  color: var(--el-text-color-placeholder);
}
.err {
  font-size: 11px;
}
.good {
  color: var(--el-color-success);
}
.mid {
  color: var(--el-color-warning-dark-2);
}
.bad {
  color: var(--el-color-danger);
}
:deep(.row-off) {
  opacity: 0.5;
}
:deep(.row-skipped td) {
  background: var(--el-color-danger-light-9) !important;
}
</style>
