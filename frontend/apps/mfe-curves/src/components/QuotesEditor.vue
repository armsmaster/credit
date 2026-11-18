<template>
  <div class="quotes-editor">
    <div class="row">
      <el-radio-group :model-value="mode" size="small" @update:model-value="switchMode">
        <el-radio-button value="bid_ask">Тенор, бид, аск</el-radio-button>
        <el-radio-button value="mid_spread">Тенор, мид, спред</el-radio-button>
      </el-radio-group>
      <el-button size="small" link type="primary" @click="insertSample">Пример</el-button>
    </div>
    <el-input
      v-model="text"
      type="textarea"
      :rows="12"
      class="mono"
      :placeholder="placeholder"
      @update:model-value="parse"
    />
    <div class="hint" :class="{ error: !!error }">
      <template v-if="error">{{ error }}</template>
      <template v-else>
        {{ count }} котировок. Ставки в процентах{{ mode === 'mid_spread' ? '; спред = аск − бид, тоже в процентах (0.20 = 20 бп)' : '' }}.
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { SAMPLE_BID_ASK, SAMPLE_MID_SPREAD } from '../samples'
import type { InputMode } from '../types'

type Row = Record<string, unknown>

const props = defineProps<{ mode: InputMode; quotes: Row[] }>()
const emit = defineEmits<{
  (e: 'update:mode', v: InputMode): void
  (e: 'update:quotes', v: Row[]): void
  (e: 'valid', v: boolean): void
}>()

const FIELDS: Record<InputMode, [string, string]> = { bid_ask: ['bid', 'ask'], mid_spread: ['mid', 'spread'] }
const text = ref(format(props.quotes))
const error = ref('')
const count = computed(() => props.quotes.length)
const placeholder = computed(() =>
  props.mode === 'bid_ask'
    ? '[\n  {"tenor": "1M", "bid": 20.70, "ask": 20.95},\n  {"tenor": "1Y", "bid": 17.70, "ask": 18.10}\n]'
    : '[\n  {"tenor": "1M", "mid": 20.825, "spread": 0.25},\n  {"tenor": "1Y", "mid": 17.90, "spread": 0.40}\n]',
)

function format(rows: Row[]): string {
  if (!rows.length) return ''
  // canonical key order (the database does not preserve it): tenor first, then the mode's fields
  const keys = ['tenor', ...FIELDS[props.mode]]
  const ordered = rows.map((r) => {
    const o: Row = {}
    for (const k of keys) if (k in r) o[k] = r[k]
    for (const k of Object.keys(r)) if (!(k in o)) o[k] = r[k]
    return o
  })
  return '[\n' + ordered.map((r) => '  ' + JSON.stringify(r)).join(',\n') + '\n]'
}

watch(
  () => props.quotes,
  (rows) => {
    // external change (prefill / mode switch): re-render only if it differs from what is typed
    if (!error.value && JSON.stringify(safeParse(text.value)) !== JSON.stringify(rows)) text.value = format(rows)
  },
)

function safeParse(s: string): unknown {
  try {
    return JSON.parse(s)
  } catch {
    return undefined
  }
}

function parse() {
  const raw = text.value.trim()
  if (!raw) {
    error.value = 'Введите котировки в формате JSON'
    emit('valid', false)
    return
  }
  let data: any
  try {
    data = JSON.parse(raw)
  } catch (e: any) {
    error.value = `Некорректный JSON: ${e.message}`
    emit('valid', false)
    return
  }
  if (data && !Array.isArray(data) && Array.isArray(data.quotes)) {
    if (data.mode === 'bid_ask' || data.mode === 'mid_spread') emit('update:mode', data.mode)
    data = data.quotes
  }
  if (!Array.isArray(data)) {
    error.value = 'Ожидается массив объектов'
    emit('valid', false)
    return
  }
  const [a, b] = FIELDS[props.mode]
  for (const [i, row] of data.entries()) {
    if (typeof row !== 'object' || row === null || !row.tenor) {
      error.value = `Строка ${i + 1}: нет поля tenor`
      emit('valid', false)
      return
    }
    for (const f of [a, b]) {
      if (typeof row[f] !== 'number') {
        error.value = `Строка ${i + 1} (${row.tenor}): поле "${f}" должно быть числом`
        emit('valid', false)
        return
      }
    }
  }
  error.value = ''
  emit('update:quotes', data)
  emit('valid', true)
}

function switchMode(mode: InputMode) {
  if (mode === props.mode) return
  const round = (x: number) => Math.round(x * 1e6) / 1e6
  const converted = props.quotes.map((q: any) =>
    mode === 'mid_spread'
      ? { tenor: q.tenor, mid: round((q.bid + q.ask) / 2), spread: round(q.ask - q.bid) }
      : { tenor: q.tenor, bid: round(q.mid - q.spread / 2), ask: round(q.mid + q.spread / 2) },
  )
  emit('update:mode', mode)
  text.value = format(converted)
  error.value = ''
  emit('update:quotes', converted)
}

function insertSample() {
  const rows = props.mode === 'bid_ask' ? SAMPLE_BID_ASK : SAMPLE_MID_SPREAD
  text.value = format(rows)
  parse()
}
</script>

<style scoped>
.quotes-editor {
  display: grid;
  gap: 6px;
}
.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.hint.error {
  color: var(--el-color-danger);
}
:deep(textarea) {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
}
</style>
