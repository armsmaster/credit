<template>
  <el-table :data="bonds" size="small" stripe :row-class-name="rowClass" :max-height="maxHeight" @row-dblclick="(r: Bond) => emit('open', r)">
    <el-table-column v-if="selectable" width="44" align="center">
      <template #header>
        <el-tooltip content="Входит в кривую"><el-icon><Select /></el-icon></el-tooltip>
      </template>
      <template #default="{ row }">
        <el-checkbox :model-value="selected.has(row.secid)" :disabled="!row.profile.eligible && !selected.has(row.secid)" @change="(v: any) => toggle(row, !!v)" />
      </template>
    </el-table-column>
    <el-table-column label="Бумага" min-width="190" fixed>
      <template #default="{ row }">
        <a class="bond-link" href="#" @click.prevent="emit('open', row)">{{ row.shortname || row.secid }}</a>
        <div class="muted small">{{ row.secid }}</div>
      </template>
    </el-table-column>
    <el-table-column label="Тип" min-width="170">
      <template #default="{ row }">
        <el-tag v-for="f in row.profile.features" :key="f" size="small" :type="FEATURE_LABELS[f]?.type || 'info'" class="tag">
          {{ FEATURE_LABELS[f]?.label || f }}
        </el-tag>
      </template>
    </el-table-column>
    <el-table-column label="Купон" width="120">
      <template #default="{ row }">{{ couponText(row) }}</template>
    </el-table-column>
    <el-table-column label="Погашение / оферта" width="150">
      <template #default="{ row }">
        <span :class="{ warn: row.short }">{{ formatDate(row.profile.effective_maturity) }}</span>
        <div class="muted small">
          <template v-if="row.years_to_maturity !== null">{{ num(row.years_to_maturity, 2) }} г.</template>
          <template v-if="row.profile.offer_date"> · оферта</template>
        </div>
      </template>
    </el-table-column>
    <el-table-column label="Оборот/день, ₽" width="150" align="right">
      <template #header>
        <el-tooltip :content="`Средний дневной оборот за ${windowDays} дн.; ниже — 20-й и 80-й перцентили (если бумага торгуется больше месяца)`">
          <span>Оборот/день, ₽ <el-icon class="help"><QuestionFilled /></el-icon></span>
        </el-tooltip>
      </template>
      <template #default="{ row }">
        <b>{{ money(row.liquidity.avg_turnover) }}</b>
        <div v-if="row.liquidity.p20_turnover !== null && row.liquidity.p20_turnover !== undefined" class="muted small">
          {{ money(row.liquidity.p20_turnover) }} … {{ money(row.liquidity.p80_turnover) }}
        </div>
      </template>
    </el-table-column>
    <el-table-column label="Сделок/день" width="100" align="right">
      <template #default="{ row }">{{ num(row.liquidity.avg_trades, 1) }}</template>
    </el-table-column>
    <el-table-column label="Бид-аск, %" width="100" align="right">
      <template #header>
        <el-tooltip content="Средний спред по итогам торгов (медиана внутридневных снимков стакана), в скобках — число дней наблюдений">
          <span>Бид-аск, % <el-icon class="help"><QuestionFilled /></el-icon></span>
        </el-tooltip>
      </template>
      <template #default="{ row }">
        {{ num(row.liquidity.avg_spread_pct, 2) }}
        <span v-if="row.liquidity.spread_days" class="muted small">({{ row.liquidity.spread_days }})</span>
      </template>
    </el-table-column>
    <el-table-column label="Статус" min-width="230">
      <template #default="{ row }">
        <div class="status">
          <el-tag v-if="row.recommended" type="success" size="small">рекомендуется</el-tag>
          <el-tag v-else-if="!row.profile.eligible" type="info" size="small">не подходит</el-tag>
          <el-tag v-else-if="!row.profile.complete" type="warning" size="small">неполные данные</el-tag>
          <el-tag v-else-if="row.short" type="warning" size="small">короткая</el-tag>
          <el-tag v-else size="small" type="info">кандидат</el-tag>
          <el-tooltip v-if="row.notes.length" placement="top">
            <template #content><div v-for="n in row.notes" :key="n">{{ n }}</div></template>
            <span class="note">{{ row.notes[0] }}<template v-if="row.notes.length > 1"> (+{{ row.notes.length - 1 }})</template></span>
          </el-tooltip>
        </div>
      </template>
    </el-table-column>
    <el-table-column v-if="showCurves" label="Кривые" width="120">
      <template #default="{ row }">
        <router-link v-for="c in row.curves" :key="c" :to="`/credit/${encodeURIComponent(c)}`" class="curve">{{ c }}</router-link>
      </template>
    </el-table-column>
    <el-table-column v-if="selectable" label="Вес" width="110">
      <template #default="{ row }">
        <el-input-number v-if="selected.has(row.secid)" v-model="weights[row.secid]" :min="0.01" :max="100" :step="0.5" size="small" controls-position="right" style="width: 90px" />
      </template>
    </el-table-column>
    <el-table-column v-if="editable" label="" width="60" align="center" fixed="right">
      <template #default="{ row }">
        <el-tooltip content="Дозаполнить / изменить данные бумаги">
          <el-button size="small" :type="row.profile.eligible && !row.profile.complete ? 'warning' : 'default'" circle @click="emit('edit', row)">
            <el-icon><EditPen /></el-icon>
          </el-button>
        </el-tooltip>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import { formatDate } from '@cds/platform'

import { couponText, FEATURE_LABELS, money, num } from '../format'
import type { Bond } from '../types'

withDefaults(
  defineProps<{
    bonds: Bond[]
    selectable?: boolean
    editable?: boolean
    showCurves?: boolean
    windowDays?: number
    maxHeight?: string | number
  }>(),
  { selectable: false, editable: false, showCurves: true, windowDays: 90, maxHeight: undefined },
)
const emit = defineEmits<{ open: [Bond]; edit: [Bond] }>()
const selected = defineModel<Set<string>>('selected', { default: () => new Set() })
const weights = defineModel<Record<string, number>>('weights', { default: () => ({}) })

function toggle(row: Bond, on: boolean) {
  const next = new Set(selected.value)
  if (on) {
    next.add(row.secid)
    if (!weights.value[row.secid]) weights.value[row.secid] = 1
  } else next.delete(row.secid)
  selected.value = next
}

function rowClass({ row }: { row: Bond }) {
  if (!row.profile.eligible) return 'row-ineligible'
  if (!row.profile.complete) return 'row-incomplete'
  return ''
}
</script>

<style scoped>
.muted {
  color: var(--el-text-color-secondary);
}
.small {
  font-size: 12px;
}
.tag {
  margin: 0 4px 2px 0;
}
.warn {
  color: var(--el-color-warning-dark-2);
  font-weight: 600;
}
.status {
  display: flex;
  gap: 6px;
  align-items: center;
}
.note {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 230px;
}
.help {
  vertical-align: middle;
}
.bond-link {
  font-weight: 600;
  text-decoration: none;
  color: var(--el-color-primary);
}
.curve {
  margin-right: 6px;
}
:deep(.row-ineligible) {
  color: var(--el-text-color-placeholder);
}
:deep(.row-incomplete td:first-child) {
  box-shadow: inset 3px 0 0 var(--el-color-warning);
}
</style>
