<template>
  <div>
    <div v-if="contract" class="facts">
      <span>Дата начала срока <b>{{ formatDate(contract.effective_date) }}</b></span>
      <span>Начало первого периода <b>{{ formatDate(contract.first_period_start) }}</b></span>
      <span>Защита с <b>{{ formatDate(contract.protection_start) }}</b> (конец дня)</span>
      <span>Первоначальный платёж <b>{{ formatDate(contract.upfront_settle_date) }}</b></span>
      <span v-if="contract.rebate_start">
        Ребейт: {{ formatDate(contract.rebate_start) }} → {{ formatDate(contract.rebate_end) }} (искл.),
        {{ rebateDays }} дн., α = {{ num(contract.rebate_year_frac, 6) }}
        <template v-if="notional"> · <b>{{ money(-coupon * notional * contract.rebate_year_frac) }} ₽</b> (платит продавец)</template>
      </span>
      <span v-else class="muted">Ребейта нет</span>
    </div>
    <el-table :data="rows" size="small" border show-summary :summary-method="summary">
      <el-table-column type="index" label="№" width="46" />
      <el-table-column label="Начало" min-width="105"><template #default="{ row }">{{ formatDate(row.accrual_start) }}</template></el-table-column>
      <el-table-column label="Конец" min-width="120">
        <template #default="{ row, $index }">{{ formatDate(row.accrual_end) }}<span v-if="$index === rows.length - 1" class="muted"> (вкл.)</span></template>
      </el-table-column>
      <el-table-column label="Платёж" min-width="105"><template #default="{ row }">{{ formatDate(row.pay_date) }}</template></el-table-column>
      <el-table-column label="Дней" min-width="60" align="right" prop="days" />
      <el-table-column label="Доля года α" min-width="100" align="right"><template #default="{ row }">{{ num(row.year_frac, 6) }}</template></el-table-column>
      <el-table-column :label="notional ? 'Купон, ₽' : 'Купон, % ном.'" min-width="130" align="right">
        <template #default="{ row }">{{ notional ? money(coupon * notional * row.year_frac) : num(coupon * row.year_frac * 100, 4) }}</template>
      </el-table-column>
      <el-table-column label="DF платежа" min-width="100" align="right"><template #default="{ row }">{{ num(row.df_pay, 6) }}</template></el-table-column>
      <el-table-column label="Дожитие Q" min-width="100" align="right">
        <template #header><el-tooltip content="Вероятность дожития до последнего дня периода"><span>Дожитие Q</span></el-tooltip></template>
        <template #default="{ row }">{{ num(row.survival, 6) }}</template>
      </el-table-column>
      <el-table-column :label="notional ? 'PV купона, ₽' : 'PV, % ном.'" min-width="120" align="right">
        <template #default="{ row }">{{ row.pv_coupon == null ? '—' : notional ? money(notional * row.pv_coupon * (coupon / std)) : num(row.pv_coupon * (coupon / std) * 100, 4) }}</template>
      </el-table-column>
      <el-table-column label="AoD" min-width="100" align="right">
        <template #header><el-tooltip content="PV начисления купона до момента дефолта на периоде"><span>AoD</span></el-tooltip></template>
        <template #default="{ row }">{{ row.pv_aod == null ? '—' : notional ? money(notional * row.pv_aod * (coupon / std)) : num(row.pv_aod * (coupon / std) * 100, 4) }}</template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { formatDate } from '@cds/platform'

import { num } from '../format'
import type { CdsContract } from '../types'

const props = withDefaults(defineProps<{ contract: CdsContract | null; coupon: number; standardCoupon?: number; notional?: number }>(), {
  standardCoupon: undefined,
  notional: 0,
})

const rows = computed(() => props.contract?.schedule ?? [])
const std = computed(() => props.standardCoupon ?? props.coupon)
const rebateDays = computed(() => {
  const c = props.contract
  if (!c?.rebate_start || !c.rebate_end) return 0
  return Math.round((new Date(c.rebate_end).getTime() - new Date(c.rebate_start).getTime()) / 86400000)
})
const money = (v: number) => v.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

function summary({ columns }: { columns: any[] }) {
  const n = props.notional
  const total = rows.value.reduce((a, r) => a + props.coupon * r.year_frac, 0)
  const pv = rows.value.reduce((a, r) => a + (r.pv_coupon ?? 0) * (props.coupon / std.value), 0)
  const aod = rows.value.reduce((a, r) => a + (r.pv_aod ?? 0) * (props.coupon / std.value), 0)
  return columns.map((_c, i) => {
    if (i === 0) return 'Итого'
    if (i === 6) return n ? money(total * n) : num(total * 100, 4)
    if (i === 9) return n ? money(pv * n) : num(pv * 100, 4)
    if (i === 10) return n ? money(aod * n) : num(aod * 100, 4)
    return ''
  })
}
</script>

<style scoped>
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
</style>
