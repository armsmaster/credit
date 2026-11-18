<template>
  <div class="charts">
    <el-card shadow="never">
      <template #header>
        <span>Форвардные ставки (помесячно){{ format === 'df' ? ', непрерывная капитализация' : '' }}</span>
      </template>
      <EChart :option="forwardOption" group="curve-charts" height="300px" />
    </el-card>
    <el-card shadow="never">
      <template #header>
        <span>{{ format === 'df' ? 'Факторы дисконтирования' : 'Бескупонная (ZC) и пар-кривая, котировки' }}</span>
      </template>
      <EChart :option="zeroOption" group="curve-charts" height="340px" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { forwardSeries, zeroSeries } from '../rates'
import type { CalibrationResult, CompareCurve, RateFormat } from '../types'
import EChart from './EChart.vue'

const props = defineProps<{
  result: CalibrationResult | null
  comparisons: CompareCurve[]
  format: RateFormat
}>()

const COMPARE_STYLE: Record<string, { name: string; color: string }> = {
  day: { name: 'Вчера', color: '#e6a23c' },
  week: { name: 'Неделя назад', color: '#909399' },
  month: { name: 'Месяц назад', color: '#b37feb' },
}
const C = { forward: '#1f4e79', zero: '#1f77b4', par: '#2ca02c', mid: '#d62728', fitted: '#111', bidask: '#c0c4cc' }

const isDf = computed(() => props.format === 'df')
const digits = computed(() => (isDf.value ? 6 : 3))

function baseOption(yName: string) {
  return {
    animation: false,
    grid: { left: 64, right: 24, top: 36, bottom: 56 },
    legend: { top: 0, type: 'scroll' },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(digits.value) : String(v)),
    },
    xAxis: { type: 'time', axisLabel: { hideOverlap: true } },
    yAxis: { type: 'value', name: yName, scale: true, axisLabel: { formatter: (v: number) => v.toFixed(isDf.value ? 3 : 2) } },
    // the mouse wheel scrolls the page; Ctrl+wheel (or the slider) zooms the chart
    dataZoom: [
      { type: 'inside', filterMode: 'none', zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false },
      { type: 'slider', height: 18, bottom: 8, filterMode: 'none' },
    ],
  }
}

const compareLabel = (c: CompareCurve) => `${COMPARE_STYLE[c.label].name} (${c.as_of_date})`

const forwardOption = computed(() => {
  const series: any[] = []
  if (props.result) {
    series.push({
      name: 'Форвард',
      type: 'line',
      step: 'end',
      showSymbol: false,
      lineStyle: { width: 2, color: C.forward },
      itemStyle: { color: C.forward },
      data: forwardSeries(props.result.nodes, props.format),
    })
  }
  for (const c of props.comparisons) {
    const st = COMPARE_STYLE[c.label]
    series.push({
      name: compareLabel(c),
      type: 'line',
      step: 'end',
      showSymbol: false,
      lineStyle: { width: 1.5, type: 'dashed', color: st.color },
      itemStyle: { color: st.color },
      data: forwardSeries(c.nodes, props.format),
    })
  }
  return { ...baseOption('%'), series }
})

const zeroOption = computed(() => {
  const series: any[] = []
  const r = props.result
  if (r) {
    series.push({
      name: isDf.value ? 'DF' : 'ZC',
      type: 'line',
      showSymbol: false,
      lineStyle: { width: 2, color: C.zero },
      itemStyle: { color: C.zero },
      data: zeroSeries(r.nodes, props.format),
    })
    if (!isDf.value) {
      series.push({
        name: 'Пар-кривая',
        type: 'line',
        showSymbol: false,
        lineStyle: { width: 1, color: C.par },
        itemStyle: { color: C.par },
        data: r.nodes.filter((n) => n.par !== null).map((n) => [n.date, n.par]),
      })
      series.push({
        name: 'Бид–аск',
        type: 'custom',
        itemStyle: { color: C.bidask },
        renderItem: (_params: any, api: any) => {
          const x = api.value(0)
          const lo = api.coord([x, api.value(1)])
          const hi = api.coord([x, api.value(2)])
          const w = 5
          const style = { stroke: '#8d9095', lineWidth: 1.5 }
          return {
            type: 'group',
            children: [
              { type: 'line', shape: { x1: lo[0], y1: lo[1], x2: hi[0], y2: hi[1] }, style },
              { type: 'line', shape: { x1: lo[0] - w, y1: lo[1], x2: lo[0] + w, y2: lo[1] }, style },
              { type: 'line', shape: { x1: hi[0] - w, y1: hi[1], x2: hi[0] + w, y2: hi[1] }, style },
            ],
          }
        },
        encode: { x: 0, y: [1, 2], tooltip: [1, 2] },
        dimensions: ['date', 'bid', 'ask'],
        data: r.fitted.map((f) => [f.maturity, f.bid, f.ask]),
        z: 3,
      })
      series.push({
        name: 'Мид рынка',
        type: 'scatter',
        symbolSize: 8,
        itemStyle: { color: C.mid },
        data: r.fitted.map((f) => [f.maturity, f.mid]),
        z: 4,
      })
      series.push({
        name: 'Скорректированный мид',
        type: 'scatter',
        symbol: 'diamond',
        symbolSize: 10,
        itemStyle: { color: C.fitted },
        data: r.fitted.map((f) => [f.maturity, f.model]),
        z: 5,
      })
    }
  }
  for (const c of props.comparisons) {
    const st = COMPARE_STYLE[c.label]
    series.push({
      name: compareLabel(c),
      type: 'line',
      showSymbol: false,
      lineStyle: { width: 1.5, type: 'dashed', color: st.color },
      itemStyle: { color: st.color },
      data: zeroSeries(c.nodes, props.format),
    })
  }
  return { ...baseOption(isDf.value ? 'DF' : '%'), series }
})
</script>

<style scoped>
.charts {
  display: grid;
  gap: 12px;
}
</style>
