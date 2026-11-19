<template>
  <div class="charts">
    <el-card shadow="never">
      <template #header><span>Форвардные hazard rates (помесячно), % годовых</span></template>
      <EChart :option="hazardOption" group="credit-charts" height="290px" />
    </el-card>
    <el-card shadow="never">
      <template #header>
        <div class="head">
          <el-radio-group v-model="view" size="small">
            <el-radio-button value="spread">Кредитный спред</el-radio-button>
            <el-radio-button value="survival">Вероятность дожития</el-radio-button>
            <el-radio-button value="pd">Накопленная PD</el-radio-button>
          </el-radio-group>
          <span class="muted">{{ VIEW_HINT[view] }}</span>
        </div>
      </template>
      <EChart :option="secondOption" group="credit-charts" height="320px" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { CalcResult, CompareCurve, Node } from '../types'
import EChart from './EChart.vue'

const props = defineProps<{ result: CalcResult | null; comparisons: CompareCurve[] }>()
type View = 'spread' | 'survival' | 'pd'
const model = defineModel<View>('view', { default: 'spread' })
const view = computed<View>({ get: () => model.value ?? 'spread', set: (v) => (model.value = v) })

const VIEW_HINT: Record<string, string> = {
  spread: 'λ̄(t)·(1−R), б.п.; точки — Z-спреды облигаций к безрисковой кривой',
  survival: 'Q(t) = exp(−∫λ), %',
  pd: '1 − Q(t), %',
}
const COMPARE_STYLE: Record<string, { name: string; color: string }> = {
  day: { name: 'Вчера', color: '#e6a23c' },
  week: { name: 'Неделя назад', color: '#909399' },
  month: { name: 'Месяц назад', color: '#b37feb' },
}

function base(yName: string, digits: number) {
  return {
    animation: false,
    grid: { left: 64, right: 24, top: 36, bottom: 56 },
    legend: { top: 0, type: 'scroll' },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(digits) : String(v)),
    },
    xAxis: { type: 'time', axisLabel: { hideOverlap: true } },
    yAxis: { type: 'value', name: yName, scale: true },
    dataZoom: [
      { type: 'inside', filterMode: 'none', zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false },
      { type: 'slider', height: 18, bottom: 8, filterMode: 'none' },
    ],
  }
}

const value = (n: Node, v: string) => (v === 'spread' ? n.spread_bp : v === 'survival' ? n.survival * 100 : n.pd * 100)
const cmpName = (c: CompareCurve) => `${COMPARE_STYLE[c.label].name} (${c.as_of_date})`

const hazardOption = computed(() => {
  const series: any[] = []
  if (props.result) {
    series.push({
      name: 'Hazard rate', type: 'line', step: 'end', showSymbol: false,
      lineStyle: { width: 2, color: '#1f4e79' }, itemStyle: { color: '#1f4e79' },
      data: props.result.nodes.map((n) => [n.date, n.hazard]),
    })
    series.push({
      name: 'Средний hazard rate', type: 'line', showSymbol: false,
      lineStyle: { width: 1, color: '#2ca02c' }, itemStyle: { color: '#2ca02c' },
      data: props.result.nodes.filter((n) => n.months > 0).map((n) => [n.date, n.avg_hazard]),
    })
  }
  for (const c of props.comparisons) {
    const st = COMPARE_STYLE[c.label]
    series.push({
      name: cmpName(c), type: 'line', step: 'end', showSymbol: false,
      lineStyle: { width: 1.5, type: 'dashed', color: st.color }, itemStyle: { color: st.color },
      data: c.nodes.map((n) => [n.date, n.hazard]),
    })
  }
  return { ...base('%', 3), series }
})

const secondOption = computed(() => {
  const v = view.value
  const series: any[] = []
  const r = props.result
  if (r) {
    series.push({
      name: v === 'spread' ? 'Спред кривой' : v === 'survival' ? 'Дожитие' : 'PD', type: 'line', showSymbol: false,
      lineStyle: { width: 2, color: '#1f77b4' }, itemStyle: { color: '#1f77b4' },
      data: r.nodes.filter((n) => v !== 'spread' || n.months > 0).map((n) => [n.date, value(n, v)]),
    })
    if (v === 'spread') {
      const used = r.fitted.filter((f) => f.used)
      series.push({
        name: 'Z-спред рынка', type: 'scatter', symbolSize: 8, itemStyle: { color: '#d62728' },
        data: used.filter((f) => f.z_spread_market_bp != null).map((f) => ({ value: [f.maturity, f.z_spread_market_bp], name: f.label })),
        tooltip: { trigger: 'item', formatter: (p: any) => `${p.name}<br/>рынок: ${p.value[1].toFixed(0)} б.п.` },
        z: 4,
      })
      series.push({
        name: 'Z-спред модели', type: 'scatter', symbol: 'diamond', symbolSize: 9, itemStyle: { color: '#111' },
        data: used.filter((f) => f.z_spread_model_bp != null).map((f) => ({ value: [f.maturity, f.z_spread_model_bp], name: f.label })),
        tooltip: { trigger: 'item', formatter: (p: any) => `${p.name}<br/>модель: ${p.value[1].toFixed(0)} б.п.` },
        z: 5,
      })
    }
  }
  for (const c of props.comparisons) {
    const st = COMPARE_STYLE[c.label]
    series.push({
      name: cmpName(c), type: 'line', showSymbol: false,
      lineStyle: { width: 1.5, type: 'dashed', color: st.color }, itemStyle: { color: st.color },
      data: c.nodes.filter((n) => v !== 'spread' || n.months > 0).map((n) => [n.date, value(n, v)]),
    })
  }
  return { ...base(v === 'spread' ? 'б.п.' : '%', v === 'spread' ? 1 : 3), series }
})
</script>

<style scoped>
.charts {
  display: grid;
  gap: 12px;
}
.head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
