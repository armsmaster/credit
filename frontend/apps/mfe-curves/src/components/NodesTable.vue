<template>
  <el-table :data="rows" size="small" height="420" border>
    <el-table-column label="Срок" width="80">
      <template #default="{ row }">{{ row.months !== null ? row.months + 'M' : '·' }}</template>
    </el-table-column>
    <el-table-column prop="date" label="Дата" width="110" />
    <el-table-column label="t, лет" align="right" width="100">
      <template #default="{ row }">{{ row.t.toFixed(4) }}</template>
    </el-table-column>
    <el-table-column label="DF" align="right">
      <template #default="{ row }">{{ row.df.toFixed(8) }}</template>
    </el-table-column>
    <el-table-column :label="`ZC, % (${short})`" align="right">
      <template #default="{ row }">{{ fmt(row.zeroRate) }}</template>
    </el-table-column>
    <el-table-column :label="`Форвард, % (${format === 'df' ? 'непр.' : short})`" align="right">
      <template #default="{ row }">{{ fmt(row.fwdRate) }}</template>
    </el-table-column>
    <el-table-column label="Пар, %" align="right">
      <template #default="{ row }">{{ fmt(row.par) }}</template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { forwardOfNode, rateFromDf } from '../rates'
import type { CurveNode, RateFormat } from '../types'

const props = defineProps<{ nodes: CurveNode[]; format: RateFormat }>()

const SHORT: Record<RateFormat, string> = {
  continuous: 'непр.',
  annual: 'год.',
  semiannual: 'полугод.',
  quarterly: 'кварт.',
  monthly: 'мес.',
  df: 'непр.',
}
const short = computed(() => SHORT[props.format])

const rows = computed(() =>
  props.nodes.map((n, j) => ({
    ...n,
    zeroRate: n.t > 0 ? rateFromDf(n.df, n.t, props.format === 'df' ? 'continuous' : props.format) : null,
    fwdRate: forwardOfNode(props.nodes, j, props.format),
  })),
)

const fmt = (v: number | null) => (v === null || v === undefined || Number.isNaN(v) ? '—' : v.toFixed(4))
</script>
