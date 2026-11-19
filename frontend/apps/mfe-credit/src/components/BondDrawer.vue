<template>
  <el-drawer :model-value="!!secid" size="62%" :title="detail ? `${detail.shortname} · ${detail.secid}` : 'Облигация'" @close="emit('close')">
    <div v-loading="loading">
      <template v-if="detail">
        <div class="head">
          <div>
            <div class="name">{{ detail.name }}</div>
            <div class="muted">
              {{ detail.board }} · номинал {{ num(detail.face_value, 2) }} {{ detail.face_unit === 'SUR' ? '₽' : detail.face_unit }}
              <template v-if="detail.initial_face && detail.face_value !== detail.initial_face"> (начальный {{ num(detail.initial_face, 0) }})</template>
              · купон {{ couponText(detail) }}
              <template v-if="detail.list_level"> · листинг {{ detail.list_level }}</template>
            </div>
            <div class="tags">
              <el-tag v-for="f in detail.profile.features" :key="f" size="small" :type="FEATURE_LABELS[f]?.type || 'info'">{{ FEATURE_LABELS[f]?.label || f }}</el-tag>
              <el-tag v-if="detail.recommended" type="success" size="small">рекомендуется</el-tag>
              <el-tag v-for="c in detail.curves" :key="c" size="small" effect="plain">в кривой {{ c }}</el-tag>
            </div>
          </div>
          <div class="spacer" />
          <el-button v-if="editable" @click="emit('edit', detail)"><el-icon><EditPen /></el-icon>&nbsp;Данные бумаги</el-button>
        </div>

        <el-alert v-for="n in detail.notes" :key="n" :title="n" type="info" :closable="false" class="note" />
        <el-alert v-for="w in detail.profile.warnings" :key="w" :title="w" type="warning" :closable="false" class="note" />

        <el-descriptions :column="3" border size="small" class="mt">
          <el-descriptions-item label="Погашение">{{ formatDate(detail.profile.mat_date) }}</el-descriptions-item>
          <el-descriptions-item label="Оферта put">{{ formatDate(detail.profile.offer_date) }}<template v-if="detail.profile.offer_date"> по {{ detail.profile.offer_price }}%</template></el-descriptions-item>
          <el-descriptions-item label="Колл">{{ formatDate(detail.profile.call_date) }}</el-descriptions-item>
          <el-descriptions-item label="Оборот/день">{{ money(detail.liquidity.avg_turnover) }} ₽</el-descriptions-item>
          <el-descriptions-item label="Перцентили 20/80">{{ money(detail.liquidity.p20_turnover) }} / {{ money(detail.liquidity.p80_turnover) }}</el-descriptions-item>
          <el-descriptions-item label="Сделок/день">{{ num(detail.liquidity.avg_trades, 1) }}</el-descriptions-item>
          <el-descriptions-item label="Бид-аск, %">{{ num(detail.liquidity.avg_spread_pct, 2) }} ({{ detail.liquidity.spread_days ?? 0 }} дн.)</el-descriptions-item>
          <el-descriptions-item label="Дней с торгами">{{ detail.liquidity.days_with_trades ?? '—' }} из {{ detail.liquidity.trading_days ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="Последняя сделка">{{ formatDate(detail.liquidity.last_trade_date) }}</el-descriptions-item>
        </el-descriptions>

        <el-tabs class="mt">
          <el-tab-pane label="DEMA (часовые свечи)" lazy>
            <div v-if="dema && dema.candles.length">
              <div class="muted small">
                Период полураспада: <b>{{ num(dema.half_life, 1) }}</b> торг. ч
                <template v-if="dema.auto">
                  (автоподбор: {{ dema.auto.pooled ? 'общий по бумагам кривых — у бумаги мало свечей' : 'по свечам бумаги' }},
                  автокорреляция ошибок прогноза {{ num(dema.auto.acf1, 2) }})
                </template>
              </div>
              <EChart :option="demaOption" height="300px" />
            </div>
            <el-empty v-else description="Свечи загружаются для бумаг, входящих в кредитные кривые" :image-size="60" />
          </el-tab-pane>
          <el-tab-pane label="Купоны">
            <el-table :data="detail.schedule.coupons || []" size="small" max-height="360">
              <el-table-column label="Начало" width="110"><template #default="{ row }">{{ formatDate(row.start) }}</template></el-table-column>
              <el-table-column label="Выплата" width="110"><template #default="{ row }">{{ formatDate(row.date) }}</template></el-table-column>
              <el-table-column label="Фиксация реестра" width="130"><template #default="{ row }">{{ formatDate(row.record_date) }}</template></el-table-column>
              <el-table-column label="Ставка, %" width="100" align="right"><template #default="{ row }">{{ num(row.valueprc, 2) }}</template></el-table-column>
              <el-table-column label="Сумма, ₽" width="110" align="right"><template #default="{ row }">{{ num(row.value, 2) }}</template></el-table-column>
              <el-table-column label="Номинал" align="right"><template #default="{ row }">{{ num(row.facevalue, 2) }}</template></el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="Амортизация и оферты">
            <el-table :data="detail.schedule.amortizations || []" size="small">
              <el-table-column label="Дата" width="120"><template #default="{ row }">{{ formatDate(row.date) }}</template></el-table-column>
              <el-table-column label="% номинала" width="110" align="right"><template #default="{ row }">{{ num(row.valueprc, 2) }}</template></el-table-column>
              <el-table-column label="Сумма, ₽" width="120" align="right"><template #default="{ row }">{{ num(row.value, 2) }}</template></el-table-column>
              <el-table-column label="Тип"><template #default="{ row }">{{ row.source === 'maturity' ? 'погашение' : 'амортизация' }}</template></el-table-column>
            </el-table>
            <el-table :data="(detail.schedule.offers || []).filter((o) => !String(o.date).startsWith('0000'))" size="small" class="mt" empty-text="Оферт нет">
              <el-table-column label="Дата оферты" width="120"><template #default="{ row }">{{ formatDate(row.date) }}</template></el-table-column>
              <el-table-column label="Цена, %" width="100" align="right"><template #default="{ row }">{{ row.price ?? '—' }}</template></el-table-column>
              <el-table-column prop="type" label="Тип" />
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="История по дням">
            <p class="muted small">Номинал и НКД — из ISS на каждый торговый день; цены на 19:00 — из собственных снимков сервиса.</p>
            <el-table :data="detail.daily" size="small" max-height="380">
              <el-table-column label="Дата" width="100" fixed><template #default="{ row }">{{ formatDate(row.trade_date) }}</template></el-table-column>
              <el-table-column label="Номинал" width="95" align="right"><template #default="{ row }">{{ num(row.face_value, 2) }}</template></el-table-column>
              <el-table-column label="НКД" width="80" align="right"><template #default="{ row }">{{ num(row.accrued, 2) }}</template></el-table-column>
              <el-table-column label="Сделок" width="75" align="right"><template #default="{ row }">{{ row.num_trades ?? '—' }}</template></el-table-column>
              <el-table-column label="Оборот" width="90" align="right"><template #default="{ row }">{{ money(row.turnover) }}</template></el-table-column>
              <el-table-column label="Close" width="75" align="right"><template #default="{ row }">{{ num(row.close, 2) }}</template></el-table-column>
              <el-table-column label="BID" width="75" align="right"><template #default="{ row }">{{ num(row.bid, 2) }}</template></el-table-column>
              <el-table-column label="ASK" width="75" align="right"><template #default="{ row }">{{ num(row.ask, 2) }}</template></el-table-column>
              <el-table-column label="LAST" width="75" align="right"><template #default="{ row }">{{ num(row.last, 2) }}</template></el-table-column>
              <el-table-column label="VWAP" width="75" align="right"><template #default="{ row }">{{ num(row.vwap ?? row.waprice, 2) }}</template></el-table-column>
              <el-table-column label="DEMA" width="80" align="right"><template #default="{ row }">{{ num(row.dema, 3) }}</template></el-table-column>
              <el-table-column label="Спред, %" align="right"><template #default="{ row }">{{ num(row.spread_pct, 2) }}</template></el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`Сегодня каждые 15 мин (${detail.intraday.length})`">
            <el-table :data="[...detail.intraday].reverse()" size="small" max-height="380" empty-text="Снимков за сегодня нет">
              <el-table-column label="Время рынка" width="120"><template #default="{ row }">{{ time(row.market_time) }}</template></el-table-column>
              <el-table-column label="BID" width="80" align="right"><template #default="{ row }">{{ num(row.bid, 2) }}</template></el-table-column>
              <el-table-column label="ASK" width="80" align="right"><template #default="{ row }">{{ num(row.ask, 2) }}</template></el-table-column>
              <el-table-column label="LAST" width="80" align="right"><template #default="{ row }">{{ num(row.last, 2) }}</template></el-table-column>
              <el-table-column label="VWAP" width="80" align="right"><template #default="{ row }">{{ num(row.vwap, 2) }}</template></el-table-column>
              <el-table-column label="DEMA" width="85" align="right"><template #default="{ row }">{{ num(row.dema, 3) }}</template></el-table-column>
              <el-table-column label="Сделок" width="80" align="right"><template #default="{ row }">{{ row.num_trades ?? '—' }}</template></el-table-column>
              <el-table-column label="Номинал" width="95" align="right"><template #default="{ row }">{{ num(row.face_value, 2) }}</template></el-table-column>
              <el-table-column label="НКД" align="right"><template #default="{ row }">{{ num(row.accrued, 2) }}</template></el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="Данные ISS">
            <el-descriptions :column="2" border size="small">
              <el-descriptions-item v-for="(v, k) in detail.desc" :key="k" :label="String(k)">{{ v }}</el-descriptions-item>
            </el-descriptions>
          </el-tab-pane>
        </el-tabs>
      </template>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, ref, watch } from 'vue'

import { formatDate } from '@cds/platform'

import { api } from '../api'
import { couponText, FEATURE_LABELS, money, num } from '../format'
import type { BondDetail } from '../types'
import EChart from './EChart.vue'

const props = withDefaults(defineProps<{ secid: string | null; editable?: boolean; version?: number }>(), { editable: false, version: 0 })
const emit = defineEmits<{ close: []; edit: [BondDetail] }>()

const detail = ref<BondDetail | null>(null)
const dema = ref<{ half_life: number | null; auto: any; candles: { begin: string; close: number; dema: number }[] } | null>(null)
const loading = ref(false)

const time = (v: string | null) => (v ? new Date(v).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : '—')

async function load() {
  if (!props.secid) {
    detail.value = null
    return
  }
  loading.value = true
  try {
    ;[detail.value, dema.value] = await Promise.all([api.bonds.get(props.secid), api.bonds.dema(props.secid)])
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}
watch(() => [props.secid, props.version], load, { immediate: true })

const demaOption = computed(() => {
  const c = dema.value?.candles ?? []
  return {
    animation: false,
    grid: { left: 56, right: 16, top: 30, bottom: 56 },
    legend: { top: 0 },
    tooltip: { trigger: 'axis', valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(3) : String(v)) },
    xAxis: { type: 'category', data: c.map((x) => x.begin.slice(5, 16).replace('T', ' ')), axisLabel: { hideOverlap: true } },
    yAxis: { type: 'value', scale: true, name: '% ном.' },
    dataZoom: [{ type: 'inside', zoomOnMouseWheel: 'ctrl', start: c.length > 300 ? 70 : 0 }, { type: 'slider', height: 18, bottom: 8 }],
    series: [
      { name: 'Close часовой свечи', type: 'scatter', symbolSize: 4, itemStyle: { color: '#909399' }, data: c.map((x) => x.close) },
      { name: 'DEMA', type: 'line', showSymbol: false, lineStyle: { width: 2, color: '#1f4e79' }, data: c.map((x) => x.dema) },
    ],
  }
})
</script>

<style scoped>
.head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.name {
  font-weight: 600;
  margin-bottom: 4px;
}
.tags {
  display: flex;
  gap: 6px;
  margin-top: 6px;
  flex-wrap: wrap;
}
.spacer {
  flex: 1;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.small {
  font-size: 12px;
}
.mt {
  margin-top: 12px;
}
.note {
  margin-top: 6px;
}
</style>
