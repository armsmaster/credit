<template>
  <div class="calc">
    <div class="inputs">
      <el-form inline>
        <el-form-item label="Дата">
          <el-date-picker v-model="target" type="date" value-format="YYYY-MM-DD" :disabled-date="beforeCurve" placeholder="Будущая дата" />
        </el-form-item>
        <el-form-item label="Форвард с даты">
          <el-date-picker v-model="start" type="date" value-format="YYYY-MM-DD" :disabled-date="beforeCurve" placeholder="необязательно" clearable />
        </el-form-item>
        <el-form-item>
          <el-button-group>
            <el-button v-for="q in quick" :key="q.label" size="small" @click="setQuick(q.months)">{{ q.label }}</el-button>
          </el-button-group>
        </el-form-item>
      </el-form>
    </div>
    <el-alert v-if="error" type="error" :title="error" :closable="false" />
    <div v-if="res" class="out">
      <div class="headline">
        <div><span class="label">t, лет ({{ dayCount }})</span><b>{{ res.t.toFixed(6) }}</b></div>
        <div><span class="label">Фактор дисконтирования</span><b>{{ res.df.toFixed(8) }}</b></div>
        <div>
          <span class="label">ZC, {{ FORMAT_LABELS[format === 'df' ? 'continuous' : format].toLowerCase() }}</span>
          <b>{{ res.zero_rates[format === 'df' ? 'continuous' : format].toFixed(4) }}%</b>
        </div>
        <div><span class="label">Мгновенный форвард (непр.)</span><b>{{ res.instantaneous_forward.toFixed(4) }}%</b></div>
      </div>
      <el-alert v-if="res.extrapolated" type="info" :closable="false" title="Дата за пределами кривой: форвард экстраполирован плоско" />
      <el-table :data="rows" size="small" border>
        <el-table-column prop="label" label="Капитализация" />
        <el-table-column label="ZC ставка, %" align="right">
          <template #default="{ row }"><span :class="{ hl: row.key === format }">{{ row.zero.toFixed(4) }}</span></template>
        </el-table-column>
        <el-table-column v-if="res.forward_rates" :label="`Форвард ${start} → ${target}, %`" align="right">
          <template #default="{ row }"><span :class="{ hl: row.key === format }">{{ row.forward?.toFixed(4) }}</span></template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { api } from '../api'
import { COMPOUNDINGS, FORMAT_LABELS } from '../rates'
import type { CurveNode, EvaluateResult, RateFormat } from '../types'

const props = defineProps<{ asOf: string; nodes: CurveNode[]; dayCount: string; format: RateFormat }>()

const target = ref<string | null>(null)
const start = ref<string | null>(null)
const res = ref<EvaluateResult | null>(null)
const error = ref('')
const quick = [
  { label: '3M', months: 3 },
  { label: '1Y', months: 12 },
  { label: '2Y', months: 24 },
  { label: '5Y', months: 60 },
  { label: '10Y', months: 120 },
]

const rows = computed(() =>
  res.value
    ? COMPOUNDINGS.map((c) => ({ key: c, label: FORMAT_LABELS[c], zero: res.value!.zero_rates[c], forward: res.value!.forward_rates?.[c] }))
    : [],
)

function beforeCurve(d: Date) {
  return d.getTime() <= new Date(props.asOf + 'T00:00:00').getTime()
}

function setQuick(months: number) {
  const d = new Date(props.asOf + 'T00:00:00')
  d.setMonth(d.getMonth() + months)
  const pad = (n: number) => String(n).padStart(2, '0')
  target.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

let seq = 0
async function evaluate() {
  if (!target.value || !props.nodes.length) {
    res.value = null
    return
  }
  const my = ++seq
  try {
    const out = await api.evaluate({
      as_of_date: props.asOf,
      day_count: props.dayCount,
      nodes: props.nodes.map((n) => ({ date: n.date, df: n.df })),
      date: target.value,
      start_date: start.value,
    })
    if (my === seq) {
      res.value = out
      error.value = ''
    }
  } catch (e: any) {
    if (my === seq) {
      error.value = e.message
      res.value = null
    }
  }
}

watch([target, start, () => props.nodes], evaluate)
</script>

<style scoped>
.calc {
  display: grid;
  gap: 8px;
}
.headline {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-bottom: 8px;
}
.headline > div {
  display: flex;
  flex-direction: column;
  background: #f7f8fa;
  border-radius: 6px;
  padding: 8px 12px;
}
.label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.hl {
  font-weight: 700;
  color: var(--el-color-primary);
}
.out {
  display: grid;
  gap: 8px;
}
</style>
