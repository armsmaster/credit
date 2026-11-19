<template>
  <el-dialog :model-value="!!bond" :title="bond ? `Данные облигации ${bond.shortname || bond.secid}` : ''" width="720px" @close="emit('close')">
    <template v-if="bond">
      <el-alert v-if="bond.profile.missing.length" type="warning" :closable="false" show-icon class="mb" title="Не хватает данных для расчёта">
        <div v-for="m in bond.profile.missing" :key="m">• {{ m }}</div>
      </el-alert>
      <el-alert v-if="bond.profile.ineligible.length" type="info" :closable="false" show-icon class="mb" title="Автоматическая проверка: не подходит для кривой">
        <div v-for="m in bond.profile.ineligible" :key="m">• {{ m }}</div>
      </el-alert>
      <p class="muted small">Пустое поле — значение из ISS ({{ iss }}). Заполненное — ручное значение, оно имеет приоритет.</p>
      <el-form label-position="top" size="small">
        <div class="grid">
          <el-form-item label="Тип купона">
            <el-select v-model="m.kind" clearable :placeholder="KIND[bond.profile.kind ?? ''] || 'из ISS'">
              <el-option v-for="(t, k) in KIND" :key="k" :value="k" :label="t" />
            </el-select>
          </el-form-item>
          <template v-if="kind === 'floating'">
            <el-form-item label="Индекс купона">
              <el-select v-model="m.coupon_index" clearable :placeholder="INDEX[bond.profile.coupon_index ?? ''] || 'не задан'">
                <el-option value="KEYRATE" label="Ключевая ставка" />
                <el-option value="RUONIA" label="RUONIA" />
              </el-select>
            </el-form-item>
            <el-form-item label="Маржа к индексу, % годовых">
              <el-input-number v-model="m.spread_pct" :step="0.05" :precision="2" controls-position="right" :placeholder="ph(bond.profile.spread_pct)" />
            </el-form-item>
            <el-form-item label="Лаг индекса, дней">
              <el-input-number v-model="m.lag_days" :min="0" :max="60" controls-position="right" placeholder="0" />
            </el-form-item>
          </template>
          <template v-if="kind === 'linker'">
            <el-form-item label="Индекс номинала">
              <el-select v-model="m.notional_index" clearable :placeholder="INDEX[bond.profile.notional_index ?? ''] || 'не задан'">
                <el-option value="RUONIA" label="RUONIA (ежедневная капитализация)" />
              </el-select>
            </el-form-item>
          </template>
          <el-form-item v-if="kind !== 'floating'" label="Ставка неизвестных купонов, % годовых">
            <el-input-number v-model="m.fixed_rate_pct" :min="0" :max="100" :step="0.05" :precision="2" controls-position="right" :placeholder="ph(bond.profile.fixed_rate_pct)" />
          </el-form-item>
          <el-form-item label="Дата оферты (put)">
            <el-date-picker v-model="m.offer_date" type="date" value-format="YYYY-MM-DD" :placeholder="bond.profile.offer_date || 'нет'" :disabled="m.ignore_offer" />
          </el-form-item>
          <el-form-item label="Цена оферты, %">
            <el-input-number v-model="m.offer_price" :min="1" :max="200" controls-position="right" placeholder="100" :disabled="m.ignore_offer" />
          </el-form-item>
          <el-form-item label="Период полураспада DEMA, торг. часов">
            <el-input-number v-model="m.dema_half_life_hours" :min="0.5" :max="1000" :step="1" controls-position="right" placeholder="авто" />
          </el-form-item>
        </div>
        <div class="flags">
          <el-checkbox v-model="m.ignore_offer">Не учитывать оферту (оценивать до погашения)</el-checkbox>
          <el-checkbox v-model="m.force_eligible" :disabled="m.excluded">Допустить, несмотря на автоматическую проверку</el-checkbox>
          <el-checkbox v-model="m.excluded">Исключить из кандидатов</el-checkbox>
        </div>
        <el-form-item label="Комментарий">
          <el-input v-model="m.comment" type="textarea" :rows="2" maxlength="1000" />
        </el-form-item>
      </el-form>
      <p v-if="bond.manual_updated_by" class="muted small">Изменено: {{ bond.manual_updated_by }}, {{ formatDateTime(bond.manual_updated_at) }}</p>
    </template>
    <template #footer>
      <el-button @click="emit('close')">Отмена</el-button>
      <el-button type="primary" :loading="saving" @click="save">Сохранить</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, ref, watch } from 'vue'

import { formatDateTime } from '@cds/platform'

import { api } from '../api'
import type { Bond, BondDetail, BondManual } from '../types'

const props = defineProps<{ bond: Bond | null }>()
const emit = defineEmits<{ close: []; saved: [BondDetail] }>()

const KIND: Record<string, string> = { fixed: 'Фиксированный', floating: 'Плавающий', linker: 'Индексируемый номинал' }
const INDEX: Record<string, string> = { KEYRATE: 'Ключевая ставка', RUONIA: 'RUONIA' }

const m = ref<BondManual>({})
const saving = ref(false)
watch(
  () => props.bond,
  (b) => {
    m.value = b ? { ...b.manual } : {}
  },
  { immediate: true },
)

const kind = computed(() => m.value.kind || props.bond?.profile.kind)
const iss = computed(() => (props.bond ? `тип: ${KIND[props.bond.profile.kind ?? ''] ?? '—'}` : ''))
const ph = (v: number | null | undefined) => (v === null || v === undefined ? 'нет' : String(v))

async function save() {
  if (!props.bond) return
  saving.value = true
  try {
    const clean: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(m.value)) if (v !== null && v !== undefined && v !== '') clean[k] = v
    const detail = await api.bonds.saveManual(props.bond.secid, clean as BondManual)
    ElMessage.success('Данные облигации сохранены')
    emit('saved', detail)
    emit('close')
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 0 12px;
}
.flags {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 12px;
}
.mb {
  margin-bottom: 12px;
}
.muted {
  color: var(--el-text-color-secondary);
}
.small {
  font-size: 12px;
}
</style>
