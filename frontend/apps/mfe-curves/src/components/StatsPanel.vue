<template>
  <div class="stats">
    <el-alert v-for="w in result.warnings" :key="w" type="warning" :title="w" :closable="false" show-icon />
    <div class="grid">
      <div class="stat">
        <div class="label">Среднее |отклонение|</div>
        <div class="value" :class="{ bad: s.mean_abs_deviation_bp > p.max_avg_deviation_bp + 0.01 }">
          {{ s.mean_abs_deviation_bp?.toFixed(2) }} <small>/ {{ p.max_avg_deviation_bp }} бп</small>
        </div>
      </div>
      <div class="stat">
        <div class="label">Макс. |отклонение|</div>
        <div class="value" :class="{ bad: s.max_abs_deviation_bp > p.max_point_deviation_bp + 0.01 }">
          {{ s.max_abs_deviation_bp?.toFixed(2) }} <small>/ {{ p.max_point_deviation_bp }} бп</small>
        </div>
      </div>
      <div class="stat">
        <div class="label">Вне бид-аск</div>
        <div class="value" :class="{ bad: s.outside_spread > 0 }">{{ s.outside_spread }} <small>из {{ result.fitted.length }}</small></div>
      </div>
      <el-tooltip content="Средний квадрат кривизны форвардной кривой, бп² на месяц² — чем меньше, тем глаже">
        <div class="stat">
          <div class="label">Шероховатость форвардов</div>
          <div class="value">{{ s.roughness?.toFixed(3) }}</div>
        </div>
      </el-tooltip>
      <div class="stat">
        <div class="label">Расчёт</div>
        <div class="value small">
          <el-tag :type="s.converged ? 'success' : 'danger'" size="small">{{ s.converged ? 'сошёлся' : 'не сошёлся' }}</el-tag>
          {{ s.elapsed_ms }} мс · {{ s.grid_nodes }} узлов
        </div>
      </div>
      <div class="stat">
        <div class="label">Параметры</div>
        <div class="value small">
          λ={{ p.smoothness }} · κ={{ p.bid_ask_penalty }} · плоско с {{ p.long_end_flat_from_months ?? 'авто' }}M
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { CalibrationResult } from '../types'

const props = defineProps<{ result: CalibrationResult }>()
const s = computed(() => props.result.stats)
const p = computed(() => props.result.params)
</script>

<style scoped>
.stats {
  display: grid;
  gap: 8px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 8px;
}
.stat {
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 8px 12px;
}
.label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.value {
  font-size: 18px;
  font-weight: 600;
}
.value.small {
  font-size: 13px;
  font-weight: 400;
}
.value small {
  font-weight: 400;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.bad {
  color: var(--el-color-danger);
}
</style>
