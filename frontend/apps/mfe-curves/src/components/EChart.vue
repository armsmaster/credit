<template>
  <div ref="el" class="echart" :style="{ height }" />
</template>

<script setup lang="ts">
import { CustomChart, LineChart, ScatterChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

echarts.use([LineChart, ScatterChart, CustomChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, TitleComponent, CanvasRenderer])

const props = withDefaults(defineProps<{ option: echarts.EChartsCoreOption; height?: string; group?: string }>(), {
  height: '340px',
  group: undefined,
})

const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

onMounted(() => {
  chart = echarts.init(el.value!)
  if (props.group) {
    chart.group = props.group
    echarts.connect(props.group)
  }
  chart.setOption(props.option, true)
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(el.value!)
})

watch(
  () => props.option,
  (opt) => chart?.setOption(opt, { notMerge: true, lazyUpdate: true }),
)

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>

<style scoped>
.echart {
  width: 100%;
}
</style>
