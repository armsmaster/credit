<template>
  <div>
    <div class="page-toolbar">
      <h2>CDS-кривые</h2>
      <div class="spacer" />
      <el-date-picker v-model="exportDate" type="date" value-format="YYYY-MM-DD" style="width: 150px" :clearable="false" />
      <el-tooltip content="Итоги дня для position keeping системы: дисконт-факторы, дожитие и hazard rate по месяцам, CDS-котировки по срокам">
        <el-button :loading="exporting" @click="exportDaily"><el-icon><Download /></el-icon>&nbsp;Файл для position keeping</el-button>
      </el-tooltip>
      <el-tooltip content="Открытый API (без авторизации, только чтение): par spread, QS и PUF на стандартных сроках — онлайн и за дату. Описание и проверка запросов — в Swagger">
        <el-button tag="a" href="/api/credit/public/docs" target="_blank" rel="noopener"><el-icon><Link /></el-icon>&nbsp;API модельных цен</el-button>
      </el-tooltip>
      <el-button v-if="isOperator" type="primary" @click="openDialog(null)"><el-icon><Plus /></el-icon>&nbsp;Контрольное лицо</el-button>
    </div>
    <p class="muted intro">Считаются автоматически по кредитным кривым облигаций при каждом сохранении кредитной или процентной кривой.</p>
    <el-alert v-if="!loading && !items.length" type="info" :closable="false" show-icon class="mb"
      title="Добавьте контрольное лицо и выберите кредитную кривую, по которой оно оценивается." />
    <el-table v-loading="loading" :data="items" stripe @row-click="(r: CdsEntity) => router.push(`/credit/cds/${encodeURIComponent(r.code)}`)">
      <el-table-column label="Контрольное лицо" min-width="200">
        <template #default="{ row }">
          <b>{{ row.code }}</b> <span class="muted">{{ row.name }}</span>
          <el-tag v-if="!row.is_active" size="small" type="info">неактивно</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="credit_curve_code" label="Кредитная кривая" width="140" />
      <el-table-column label="Купон" width="70" align="right"><template #default="{ row }">{{ row.standard_coupon_bp }}</template></el-table-column>
      <el-table-column label="RR" width="70" align="right">
        <template #default="{ row }">{{ row.state ? Math.round(row.state.recovery * 100) + '%' : '—' }}</template>
      </el-table-column>
      <el-table-column label="Кривая на" width="170">
        <template #default="{ row }">
          <template v-if="row.state">
            {{ formatDate(row.state.as_of_date) }}
            <el-tag v-if="row.state.as_of_date === today" size="small" type="success">сегодня</el-tag>
            <div class="muted small">{{ row.state.trigger_title }}</div>
          </template>
          <span v-else class="muted">не рассчитана</span>
        </template>
      </el-table-column>
      <el-table-column v-for="t in shownTenors" :key="t" :label="`${t} par / PUF`" width="125" align="right">
        <template #header>
          <el-tooltip :content="`Par spread, б.п. / PUF при стандартном купоне, %`"><span>{{ t }} par / PUF</span></el-tooltip>
        </template>
        <template #default="{ row }">
          <template v-if="tenorOf(row, t)">
            <b>{{ num(tenorOf(row, t)!.par_spread_bp, 1) }}</b>
            <div class="muted small">{{ num((tenorOf(row, t)!.puf[String(row.standard_coupon_bp)] ?? 0) * 100, 3) }}%</div>
          </template>
        </template>
      </el-table-column>
      <el-table-column label="" width="90" align="center">
        <template #default="{ row }">
          <el-tooltip v-if="row.state?.warnings?.length" placement="top">
            <template #content><div v-for="w in row.state.warnings" :key="w">{{ w }}</div></template>
            <el-icon color="var(--el-color-warning)"><WarningFilled /></el-icon>
          </el-tooltip>
          <el-button v-if="isOperator" size="small" circle @click.stop="openDialog(row)"><el-icon><EditPen /></el-icon></el-button>
        </template>
      </el-table-column>
    </el-table>
    <CdsEntityDialog :open="dialogOpen" :entity="editing" :tenors="tenors" @close="dialogOpen = false" @saved="load" />
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { formatDate, getPlatform, Role } from '@cds/platform'

import { api } from '../api'
import CdsEntityDialog from '../components/CdsEntityDialog.vue'
import { num } from '../format'
import type { CdsEntity } from '../types'

const router = useRouter()
const isOperator = getPlatform().hasRole(Role.Operator)
const items = ref<CdsEntity[]>([])
const loading = ref(false)
const today = ref('')
const tenors = ref<string[]>(['6M', '1Y', '2Y', '3Y', '4Y', '5Y'])
const shownTenors = ['1Y', '3Y', '5Y']
const exportDate = ref('')
const exporting = ref(false)
const dialogOpen = ref(false)
const editing = ref<CdsEntity | null>(null)

const tenorOf = (row: CdsEntity, t: string) => row.state?.tenors.find((x) => x.tenor === t)

async function load() {
  loading.value = true
  try {
    const [meta, list] = await Promise.all([api.cds.meta(), api.cds.entities()])
    today.value = meta.today
    tenors.value = meta.settings.tenors
    if (!exportDate.value) exportDate.value = meta.today
    items.value = list
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function openDialog(e: CdsEntity | null) {
  editing.value = e
  dialogOpen.value = true
}

async function exportDaily() {
  exporting.value = true
  try {
    await api.cds.excel.daily(exportDate.value)
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    exporting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.intro {
  margin: -4px 0 12px;
  font-size: 13px;
}
.page-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.page-toolbar h2 {
  margin: 0;
}
.spacer {
  flex: 1;
}
.muted {
  color: var(--el-text-color-secondary);
}
.small {
  font-size: 12px;
}
.mb {
  margin-bottom: 12px;
}
:deep(.el-table__row) {
  cursor: pointer;
}
</style>
