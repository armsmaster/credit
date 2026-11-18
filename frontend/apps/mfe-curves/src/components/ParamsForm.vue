<template>
  <el-form label-position="top" size="small" class="params">
    <div class="two">
      <el-form-item label="Предельное среднее отклонение, бп">
        <el-input-number v-model="model.max_avg_deviation_bp" :min="0" :max="500" :step="0.5" controls-position="right" />
        <Reset field="max_avg_deviation_bp" />
      </el-form-item>
      <el-form-item label="Предельное отклонение точки, бп">
        <el-input-number v-model="model.max_point_deviation_bp" :min="0" :max="1000" :step="1" controls-position="right" />
        <Reset field="max_point_deviation_bp" />
      </el-form-item>
    </div>

    <el-form-item>
      <template #label>
        <span>Гладкость λ = {{ model.smoothness }}</span>
        <el-tooltip content="Главная «ручка»: чем больше λ, тем глаже форвардная кривая и тем дальше скорректированные миды от рыночных (в пределах ограничений)">
          <el-icon class="help"><QuestionFilled /></el-icon>
        </el-tooltip>
      </template>
      <div class="slider-row">
        <el-slider v-model="logLambda" :min="-2" :max="4" :step="0.05" :format-tooltip="(v: number) => fmtLambda(10 ** v)" />
        <Reset field="smoothness" />
      </div>
    </el-form-item>

    <el-collapse>
      <el-collapse-item title="Дополнительные параметры" name="more">
        <div class="two">
          <el-form-item label="Штраф за выход за бид-аск, κ">
            <el-input-number v-model="model.bid_ask_penalty" :min="0" :step="1" controls-position="right" />
            <Reset field="bid_ask_penalty" />
          </el-form-item>
          <el-form-item label="Вес плоского дальнего конца, μ">
            <el-input-number v-model="model.long_end_flatness" :min="0" :step="1" controls-position="right" />
            <Reset field="long_end_flatness" />
          </el-form-item>
          <el-form-item label="Плоский конец с, мес. (пусто = авто)">
            <el-input-number v-model="flatFrom" :min="1" :max="600" controls-position="right" placeholder="авто" />
            <Reset field="long_end_flat_from_months" />
          </el-form-item>
          <el-form-item label="Короткий конец, мес.">
            <el-input-number v-model="model.short_end_months" :min="0" :max="120" controls-position="right" />
            <Reset field="short_end_months" />
          </el-form-item>
          <el-form-item label="Вес гладкости короткого конца (0–1]">
            <el-input-number v-model="model.short_end_weight" :min="0.001" :max="1" :step="0.05" :precision="3" controls-position="right" />
            <Reset field="short_end_weight" />
          </el-form-item>
        </div>
        <p class="note">
          «Авто» для плоского конца — срок предпоследней котировки. На коротком конце кривизна штрафуется с весом
          короткого конца и линейно доходит до 1 к удвоенному сроку (траектория ключевой ставки делает его изогнутым).
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

const fmtLambda = (v: number) => (v >= 10 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : v.toFixed(2))

const logLambda = computed({
  get: () => Math.log10(Math.max(model.value.smoothness, 0.01)),
  set: (v: number) => {
    model.value.smoothness = Number(fmtLambda(10 ** v))
  },
})

const flatFrom = computed({
  get: () => model.value.long_end_flat_from_months ?? undefined,
  set: (v: number | undefined | null) => {
    model.value.long_end_flat_from_months = v ?? null
  },
})

// Small "reset to curve default" link shown only when a value is overridden.
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
