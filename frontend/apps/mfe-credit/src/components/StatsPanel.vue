<template>
  <el-card shadow="never">
    <div class="stats">
      <div class="stat"><span>RMSE</span><b>{{ num(result.stats.rmse_bp, 1) }} б.п.</b></div>
      <div class="stat"><span>Среднее |ошибка|</span><b>{{ num(result.stats.mean_abs_bp, 1) }} б.п.</b></div>
      <div class="stat"><span>Макс. |ошибка|</span><b>{{ num(result.stats.max_abs_bp, 1) }} б.п.</b></div>
      <div class="stat"><span>Бумаг / цен</span><b>{{ result.stats.n_bonds }} / {{ result.stats.n_observations }}</b></div>
      <div class="stat"><span>Плоско с</span><b>{{ result.stats.flat_from_months }} мес.</b></div>
      <div class="stat"><span>Recovery</span><b>{{ Math.round(result.params.recovery_rate * 100) }}%</b></div>
      <div class="stat"><span>Расчёт</span><b>{{ result.stats.iterations }} итер., {{ num(result.stats.elapsed_ms, 0) }} мс</b></div>
    </div>
    <div v-if="inputs" class="curves muted">
      Дисконтирование: <b>{{ inputs.discount?.code }}</b> от {{ formatDate(inputs.discount?.as_of_date) }}
      <template v-for="(ref_, idx) in inputs.projection ?? {}" :key="idx">
        · {{ idx === 'KEYRATE' ? 'ключевая ставка' : idx }}: <b>{{ ref_.code }}</b> от {{ formatDate(ref_.as_of_date) }}
      </template>
    </div>
    <el-alert v-for="w in warnings" :key="w" :title="w" type="warning" :closable="false" class="warn" />
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { formatDate } from '@cds/platform'

import { num } from '../format'
import type { CalcInputs, CalcResult } from '../types'

const props = defineProps<{ result: CalcResult; inputs?: CalcInputs | null }>()
const warnings = computed(() => [...new Set([...(props.result.warnings ?? []), ...(props.inputs?.warnings ?? [])])])
</script>

<style scoped>
.stats {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
}
.stat {
  display: flex;
  flex-direction: column;
}
.stat span {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.curves {
  margin-top: 8px;
  font-size: 13px;
}
.muted {
  color: var(--el-text-color-secondary);
}
.warn {
  margin-top: 6px;
}
</style>
