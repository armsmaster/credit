<template>
  <div>
    <el-checkbox v-model="monthly" class="mb">Все месяцы (по умолчанию — по годам)</el-checkbox>
    <el-table :data="rows" size="small" max-height="460">
      <el-table-column label="Дата" width="110"><template #default="{ row }">{{ formatDate(row.date) }}</template></el-table-column>
      <el-table-column label="Срок" width="80"><template #default="{ row }">{{ tenor(row.months) }}</template></el-table-column>
      <el-table-column label="Hazard (форвард), %" align="right"><template #default="{ row }">{{ num(row.hazard, 3) }}</template></el-table-column>
      <el-table-column label="Средний hazard, %" align="right"><template #default="{ row }">{{ row.months ? num(row.avg_hazard, 3) : '—' }}</template></el-table-column>
      <el-table-column label="Дожитие Q(t)" align="right"><template #default="{ row }">{{ num(row.survival, 5) }}</template></el-table-column>
      <el-table-column label="PD, %" align="right"><template #default="{ row }">{{ num(row.pd * 100, 3) }}</template></el-table-column>
      <el-table-column label="Спред λ̄(1−R), б.п." align="right"><template #default="{ row }">{{ row.months ? num(row.spread_bp, 1) : '—' }}</template></el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import { formatDate } from '@cds/platform'

import { num } from '../format'
import type { Node } from '../types'

const props = defineProps<{ nodes: Node[] }>()
const monthly = ref(false)
const rows = computed(() => (monthly.value ? props.nodes : props.nodes.filter((n) => n.months % 12 === 0 || n.months === 6 || n.months === 3)))
const tenor = (m: number) => (m === 0 ? '0' : m % 12 === 0 ? `${m / 12}Y` : `${m}M`)
</script>

<style scoped>
.mb {
  margin-bottom: 8px;
}
</style>
