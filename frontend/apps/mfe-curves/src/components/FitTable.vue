<template>
  <el-table :data="fitted" size="small" border>
    <el-table-column prop="tenor" label="Тенор" width="70" />
    <el-table-column label="Погашение" width="105">
      <template #default="{ row }">{{ row.maturity }}</template>
    </el-table-column>
    <el-table-column label="Бид, %" align="right">
      <template #default="{ row }">{{ row.bid.toFixed(3) }}</template>
    </el-table-column>
    <el-table-column label="Аск, %" align="right">
      <template #default="{ row }">{{ row.ask.toFixed(3) }}</template>
    </el-table-column>
    <el-table-column label="Мид рынка, %" align="right">
      <template #default="{ row }">{{ row.mid.toFixed(4) }}</template>
    </el-table-column>
    <el-table-column label="Скорр. мид, %" align="right">
      <template #default="{ row }"><b>{{ row.model.toFixed(4) }}</b></template>
    </el-table-column>
    <el-table-column label="Отклонение, бп" align="right" width="130">
      <template #default="{ row }">
        <span :class="devClass(row)">{{ row.deviation_bp >= 0 ? '+' : '' }}{{ row.deviation_bp.toFixed(2) }}</span>
      </template>
    </el-table-column>
    <el-table-column label="½ спреда, бп" align="right" width="110">
      <template #default="{ row }">{{ row.half_spread_bp.toFixed(1) }}</template>
    </el-table-column>
    <el-table-column label="В бид-аск" align="center" width="90">
      <template #default="{ row }">
        <el-icon v-if="row.in_spread" color="var(--el-color-success)"><CircleCheck /></el-icon>
        <el-icon v-else color="var(--el-color-danger)"><Warning /></el-icon>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import type { FittedQuote, Params } from '../types'

const props = defineProps<{ fitted: FittedQuote[]; params?: Params }>()

function devClass(row: FittedQuote) {
  const limit = props.params?.max_point_deviation_bp ?? Infinity
  const d = Math.abs(row.deviation_bp)
  if (d > limit + 0.01 || !row.in_spread) return 'bad'
  if (d > row.half_spread_bp * 0.5) return 'warn'
  return 'ok'
}
</script>

<style scoped>
.bad {
  color: var(--el-color-danger);
  font-weight: 600;
}
.warn {
  color: var(--el-color-warning);
}
.ok {
  color: var(--el-color-success);
}
</style>
