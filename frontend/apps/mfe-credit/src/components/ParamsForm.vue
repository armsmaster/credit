<template>
  <el-form label-position="top" size="small" class="params">
    <div class="two">
      <el-form-item label="Recovery rate, %">
        <el-input-number v-model="recoveryPct" :min="0" :max="95" :step="5" controls-position="right" />
        <Reset field="recovery_rate" />
      </el-form-item>
      <el-form-item>
        <template #label>
          <span>Короткая бумага, лет</span>
          <el-tooltip content="Бумаги, до погашения или оферты которых осталось меньше, система предлагает исключить из кривой">
            <el-icon class="help"><QuestionFilled /></el-icon>
          </el-tooltip>
        </template>
        <el-input-number v-model="model.short_bond_years" :min="0" :max="5" :step="0.25" :precision="2" controls-position="right" />
        <Reset field="short_bond_years" />
      </el-form-item>
    </div>

    <el-form-item>
      <template #label>
        <span>Гладкость α = {{ model.smoothness }}</span>
        <el-tooltip content="Главная «ручка»: чем больше α, тем глаже кривая форвардных hazard rates и тем дальше модельные цены от облака рыночных">
          <el-icon class="help"><QuestionFilled /></el-icon>
        </el-tooltip>
      </template>
      <div class="slider-row">
        <el-slider v-model="logAlpha" :min="-1" :max="4" :step="0.05" :format-tooltip="(v: number) => fmt(10 ** v)" />
        <Reset field="smoothness" />
      </div>
    </el-form-item>

    <el-form-item>
      <template #label>
        <span>Вес LAST и VWAP = {{ model.secondary_weight }}</span>
        <el-tooltip content="Приоритет 1 — DEMA, BID, ASK (вес 1); приоритет 2 — LAST, VWAP (этот вес)">
          <el-icon class="help"><QuestionFilled /></el-icon>
        </el-tooltip>
      </template>
      <div class="slider-row">
        <el-slider v-model="model.secondary_weight" :min="0" :max="1" :step="0.05" />
        <Reset field="secondary_weight" />
      </div>
    </el-form-item>

    <el-collapse>
      <el-collapse-item title="Дальний конец" name="more">
        <div class="two">
          <el-form-item label="Плоский конец с, мес. (пусто = авто)">
            <el-input-number v-model="flatFrom" :min="1" :max="120" controls-position="right" placeholder="авто" />
            <Reset field="long_end_flat_from_months" />
          </el-form-item>
          <el-form-item label="Вес плоского конца, β">
            <el-input-number v-model="model.long_end_flatness" :min="0" :step="10" controls-position="right" />
            <Reset field="long_end_flatness" />
          </el-form-item>
        </div>
        <p class="note">
          «Авто» — с месяца последнего погашения/оферты в составе: дальше данных нет, hazard rate продолжается плоско.
          После 10 лет кривая плоская всегда.
        </p>
      </el-collapse-item>
    </el-collapse>
  </el-form>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, resolveComponent } from 'vue'

import type { Params } from '../types'

const model = defineModel<Params>({ required: true })
const props = defineProps<{ defaults: Params }>()

const fmt = (v: number) => (v >= 10 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : v.toFixed(2))

const logAlpha = computed({
  get: () => Math.log10(Math.max(model.value.smoothness, 0.1)),
  set: (v: number) => {
    model.value.smoothness = Number(fmt(10 ** v))
  },
})

const recoveryPct = computed({
  get: () => Math.round(model.value.recovery_rate * 1000) / 10,
  set: (v: number | undefined) => {
    model.value.recovery_rate = (v ?? 0) / 100
  },
})

const flatFrom = computed({
  get: () => model.value.long_end_flat_from_months ?? undefined,
  set: (v: number | undefined | null) => {
    model.value.long_end_flat_from_months = v ?? null
  },
})

const Reset = defineComponent({
  props: { field: { type: String, required: true } },
  setup(p) {
    return () => {
      const key = p.field as keyof Params
      if (model.value[key] === props.defaults[key]) return null
      return h(
        resolveComponent('el-link') as any,
        { type: 'info', class: 'reset', title: `По умолчанию: ${props.defaults[key] ?? 'авто'}`, onClick: () => ((model.value as any)[key] = props.defaults[key]) },
        () => 'сброс',
      )
    }
  },
})
</script>

<style scoped>
.two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 12px;
}
.slider-row {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 0 8px;
}
.help {
  margin-left: 4px;
  vertical-align: middle;
}
.note {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin: 0;
}
:deep(.reset) {
  margin-left: 8px;
  font-size: 12px;
}
</style>
