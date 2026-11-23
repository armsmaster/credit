<template>
  <el-dialog :model-value="open" :title="entity ? `Контрольное лицо ${entity.code}` : 'Новое контрольное лицо CDS'" width="760px" @close="emit('close')">
    <el-form label-position="top" size="default">
      <div class="grid3">
        <el-form-item label="Код" required>
          <el-input v-model="f.code" :disabled="!!entity" placeholder="GAZP" />
        </el-form-item>
        <el-form-item label="Название" required class="span2">
          <el-input v-model="f.name" placeholder="ПАО «Газпром»" />
        </el-form-item>
        <el-form-item label="ИНН"><el-input v-model="f.inn" /></el-form-item>
        <el-form-item label="ОГРН"><el-input v-model="f.ogrn" /></el-form-item>
        <el-form-item label="LEI"><el-input v-model="f.lei" /></el-form-item>
        <el-form-item label="Контрольное обязательство (ISIN)"><el-input v-model="f.reference_obligation" /></el-form-item>
        <el-form-item label="Кредитная кривая (облигации)" required class="span2">
          <el-select v-model="f.credit_curve_code" filterable style="width: 100%">
            <el-option v-for="c in curves" :key="c.code" :value="c.code" :label="`${c.code} — ${c.issuer_title}`" />
          </el-select>
        </el-form-item>
        <el-form-item label="Стандартный спред (купон), б.п.">
          <el-radio-group v-model="f.standard_coupon_bp">
            <el-radio-button :value="100">100</el-radio-button>
            <el-radio-button :value="500">500</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="Максимальный срок">
          <el-select v-model="f.max_tenor"><el-option v-for="t in tenors" :key="t" :value="t" /></el-select>
        </el-form-item>
        <el-form-item>
          <template #label>
            <span>Recovery, % (пусто — из кредитной кривой)</span>
          </template>
          <el-input-number v-model="recoveryPct" :min="0" :max="95" :step="5" controls-position="right" placeholder="из кривой" />
        </el-form-item>
        <el-form-item>
          <template #label>
            <el-tooltip content="Добавка к par spread для сделок по документации Сбера (ноль порога Неплатежа, поставка): кривая перестраивается перед расчётом контракта Сбера">
              <span>Документационный базис Сбера, б.п. <el-icon><QuestionFilled /></el-icon></span>
            </el-tooltip>
          </template>
          <el-input-number v-model="f.doc_basis_bp" :min="-1000" :max="1000" :step="5" controls-position="right" />
        </el-form-item>
      </div>
      <el-form-item>
        <template #label>
          <el-tooltip content="b(T) прибавляется к модельному par spread из облигаций перед бутстрапом CDS-кривой; обычно отрицателен (минус премия за ликвидность облигаций)">
            <span>Базис CDS–облигация b(T) по срокам, б.п. <el-icon><QuestionFilled /></el-icon></span>
          </el-tooltip>
        </template>
        <div class="basis">
          <div v-for="t in tenors" :key="t" class="basis-cell">
            <span>{{ t }}</span>
            <el-input-number v-model="f.basis_bp[t]" :step="5" size="small" controls-position="right" placeholder="0" />
          </div>
        </div>
      </el-form-item>
      <el-checkbox v-model="f.is_active">Активно (пересчитывается автоматически)</el-checkbox>
    </el-form>
    <template #footer>
      <el-button @click="emit('close')">Отмена</el-button>
      <el-button type="primary" :loading="saving" @click="save">Сохранить и пересчитать</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, ref, watch } from 'vue'

import { api } from '../api'
import type { CdsEntity, Definition } from '../types'

const props = defineProps<{ open: boolean; entity: CdsEntity | null; tenors: string[] }>()
const emit = defineEmits<{ close: []; saved: [CdsEntity] }>()

const curves = ref<Definition[]>([])
const saving = ref(false)
const blank = () => ({
  code: '', name: '', inn: '', ogrn: '', lei: '', reference_obligation: '', credit_curve_code: '', standard_coupon_bp: 100,
  max_tenor: '5Y', recovery_override: null as number | null, doc_basis_bp: 0, basis_bp: {} as Record<string, number | undefined>, is_active: true,
})
const f = ref(blank())

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    f.value = props.entity ? { ...blank(), ...props.entity, basis_bp: { ...props.entity.basis_bp } } as any : blank()
    if (!curves.value.length) curves.value = await api.definitions.list()
  },
)

const recoveryPct = computed({
  get: () => (f.value.recovery_override == null ? undefined : Math.round(f.value.recovery_override * 1000) / 10),
  set: (v: number | undefined | null) => {
    f.value.recovery_override = v == null ? null : v / 100
  },
})

async function save() {
  saving.value = true
  try {
    const body: Record<string, unknown> = { ...f.value }
    for (const k of ['inn', 'ogrn', 'lei', 'reference_obligation']) if (!body[k]) body[k] = null
    body.basis_bp = Object.fromEntries(Object.entries(f.value.basis_bp).filter(([, v]) => v != null && v !== 0))
    const saved = props.entity ? await api.cds.updateEntity(props.entity.code, body) : await api.cds.createEntity(body)
    if (saved.warning) ElMessage.warning(`Сохранено, но кривая не рассчитана: ${saved.warning}`)
    else ElMessage.success('Сохранено, CDS-кривая пересчитана')
    emit('saved', saved)
    emit('close')
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.grid3 {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 0 12px;
}
.span2 {
  grid-column: span 2;
}
.basis {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.basis-cell {
  display: flex;
  flex-direction: column;
  font-size: 12px;
  width: 100px;
}
</style>
