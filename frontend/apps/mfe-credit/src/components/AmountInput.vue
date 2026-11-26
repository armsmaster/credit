<template>
  <div class="amount-input" :class="{ inline: inlineCaption }">
    <el-input
      :model-value="text"
      :size="size"
      :placeholder="placeholder"
      :formatter="group"
      :parser="digits"
      inputmode="numeric"
      :style="{ width }"
      @update:model-value="onInput"
      @blur="onBlur"
    >
      <template v-if="suffix" #suffix>{{ suffix }}</template>
    </el-input>
    <div v-if="caption" class="amount-caption">{{ inlineCaption ? '= ' : '' }}{{ caption }}</div>
  </div>
</template>

<script setup lang="ts">
// A whole ruble amount: digits grouped by three while typing (1 000 000 000) and the amount in words
// of its order under the field (1 млрд ₽). The model stays a plain number.
import { computed, ref, watch } from 'vue'

import { amountShort } from '../format'

const props = withDefaults(
  defineProps<{
    modelValue: number | null | undefined
    min?: number
    size?: 'small' | 'default' | 'large'
    width?: string
    placeholder?: string
    suffix?: string
    inlineCaption?: boolean
  }>(),
  { min: 1, size: 'default', width: '100%', placeholder: '', suffix: '₽', inlineCaption: false },
)
const emit = defineEmits<{ 'update:modelValue': [value: number | null] }>()

const digits = (v: string | number | null | undefined) => String(v ?? '').replace(/\D/g, '')
const group = (v: string | number | null | undefined) => digits(v).replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
const toText = (v: number | null | undefined) => (v === null || v === undefined || !Number.isFinite(v) ? '' : String(Math.round(v)))

const text = ref(toText(props.modelValue))
let lastValid = props.modelValue ?? null

watch(
  () => props.modelValue,
  (v) => {
    if ((text.value === '' ? null : Number(text.value)) !== v) text.value = toText(v)
    if (v !== null && v !== undefined && v >= props.min) lastValid = v
  },
)

function onInput(v: string) {
  text.value = digits(v).replace(/^0+(?=\d)/, '')
  emit('update:modelValue', text.value === '' ? null : Number(text.value))
}

function onBlur() {
  // an empty or too small amount falls back to the last valid one, like el-input-number does
  const v = text.value === '' ? null : Number(text.value)
  if ((v === null || v < props.min) && lastValid !== null) {
    text.value = toText(lastValid)
    emit('update:modelValue', lastValid)
  }
}

const caption = computed(() => amountShort(text.value === '' ? null : Number(text.value), props.suffix || '₽'))
</script>

<style scoped>
.amount-input:not(.inline) {
  width: 100%;
}
.amount-input.inline {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.amount-caption {
  font-size: 12px;
  line-height: 1.3;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}
.inline .amount-caption {
  margin-top: 0;
  white-space: nowrap;
}
</style>
