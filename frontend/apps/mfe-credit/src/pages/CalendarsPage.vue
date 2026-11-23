<template>
  <div>
    <div class="page-toolbar">
      <h2>Календари рабочих дней</h2>
      <el-radio-group v-model="code" @change="load">
        <el-radio-button v-for="c in calendars" :key="c.code" :value="c.code">{{ c.name }}</el-radio-button>
      </el-radio-group>
      <el-input-number v-model="year" :min="2020" :max="2100" controls-position="right" style="width: 120px" @change="load" />
      <div class="spacer" />
      <div class="legend">
        <span class="sw holiday" /> нерабочий будний <span class="sw working" /> рабочий выходной
        <template v-if="current?.base_code"><span class="sw inherited" /> из календаря «{{ baseName }}»</template>
      </div>
    </div>
    <el-alert v-if="current" :title="current.description" type="info" :closable="false" class="mb" />
    <el-alert v-if="!loading && !own.size && !base.size" type="warning" :closable="false" show-icon class="mb"
      :title="`За ${year} год исключений нет: рабочими считаются все будни. Внесите праздники (клик по дню).`" />

    <div class="layout">
      <div class="months">
        <div v-for="(m, mi) in months" :key="mi" class="month">
          <div class="month-title">{{ MONTHS[mi] }}</div>
          <div class="week head"><span v-for="d in WEEK" :key="d">{{ d }}</span></div>
          <div v-for="(w, wi) in m" :key="wi" class="week">
            <span v-for="(cell, ci) in w" :key="ci" :class="cellClass(cell)" :title="cellTitle(cell)" @click="cell && pick(cell)">{{ cell?.getDate() ?? '' }}</span>
          </div>
        </div>
      </div>
      <div class="side">
        <el-card shadow="never" class="mb">
          <template #header><b>Сдвиг на рабочие дни</b></template>
          <div class="calc">
            <el-date-picker v-model="calcDate" type="date" value-format="YYYY-MM-DD" style="width: 140px" />
            <span>+</span>
            <el-input-number v-model="calcDays" :min="-60" :max="60" controls-position="right" style="width: 100px" />
            <span>р.д.</span>
            <el-button @click="shift">=</el-button>
          </div>
          <div v-if="shiftResult" class="muted small mt">
            {{ formatDate(shiftResult.result) }} · сам день {{ shiftResult.is_business ? 'рабочий' : 'нерабочий' }}, «Следующий» → {{ formatDate(shiftResult.following) }}
          </div>
        </el-card>
        <el-card shadow="never">
          <template #header><b>Исключения {{ year }} ({{ own.size }})</b></template>
          <el-table :data="ownList" size="small" max-height="460" empty-text="Нет">
            <el-table-column label="Дата" width="96"><template #default="{ row }">{{ formatDate(row.day) }}</template></el-table-column>
            <el-table-column label="День" width="88"><template #default="{ row }">{{ row.is_business ? 'рабочий' : 'нерабочий' }}</template></el-table-column>
            <el-table-column prop="comment" label="Комментарий" />
            <el-table-column v-if="isOperator" width="44">
              <template #default="{ row }"><el-button size="small" text type="danger" @click="remove(row.day)"><el-icon><Delete /></el-icon></el-button></template>
            </el-table-column>
          </el-table>
        </el-card>
      </div>
    </div>

    <el-dialog v-model="dialog" :title="dialogDay ? formatDate(dialogDay) : ''" width="420px">
      <el-form label-position="top">
        <el-form-item label="День">
          <el-radio-group v-model="dialogBusiness">
            <el-radio-button :value="false">нерабочий</el-radio-button>
            <el-radio-button :value="true">рабочий</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="Комментарий"><el-input v-model="dialogComment" placeholder="Например, «День народного единства»" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button v-if="own.has(dialogDay)" type="danger" plain @click="remove(dialogDay); dialog = false">Убрать исключение</el-button>
        <el-button @click="dialog = false">Отмена</el-button>
        <el-button type="primary" @click="save">Сохранить</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'

import { formatDate, getPlatform, Role } from '@cds/platform'

import { api } from '../api'
import type { CalendarDay, CalendarInfo } from '../types'

const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']
const WEEK = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']

const isOperator = getPlatform().hasRole(Role.Operator)
const calendars = ref<CalendarInfo[]>([])
const code = ref('MOSCOW')
const year = ref(new Date().getFullYear())
const own = ref(new Map<string, CalendarDay>())
const base = ref(new Map<string, CalendarDay>())
const loading = ref(false)
const dialog = ref(false)
const dialogDay = ref('')
const dialogBusiness = ref(false)
const dialogComment = ref('')
const calcDate = ref(new Date().toISOString().slice(0, 10))
const calcDays = ref(1)
const shiftResult = ref<{ result: string; following: string; is_business: boolean } | null>(null)

const current = computed(() => calendars.value.find((c) => c.code === code.value))
const baseName = computed(() => calendars.value.find((c) => c.code === current.value?.base_code)?.name ?? '—')
const ownList = computed(() => [...own.value.values()])

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const months = computed(() =>
  Array.from({ length: 12 }, (_, m) => {
    const first = new Date(year.value, m, 1)
    const cells: (Date | null)[] = Array.from({ length: (first.getDay() + 6) % 7 }, () => null)
    for (let d = 1; d <= new Date(year.value, m + 1, 0).getDate(); d++) cells.push(new Date(year.value, m, d))
    while (cells.length % 7) cells.push(null)
    return Array.from({ length: cells.length / 7 }, (_, w) => cells.slice(w * 7, w * 7 + 7))
  }),
)

function cellClass(d: Date | null) {
  if (!d) return 'empty'
  const key = iso(d)
  const weekend = d.getDay() === 0 || d.getDay() === 6
  const e = own.value.get(key)
  const b = base.value.get(key)
  return {
    day: true,
    weekend,
    holiday: e ? !e.is_business : false,
    working: e ? e.is_business && weekend : false,
    inherited: !e && !!b,
    'inherited-off': !e && !!b && !b.is_business,
  }
}

function cellTitle(d: Date | null) {
  if (!d) return ''
  const e = own.value.get(iso(d)) ?? base.value.get(iso(d))
  return e ? `${e.is_business ? 'рабочий' : 'нерабочий'}${e.comment ? ': ' + e.comment : ''}` : ''
}

function pick(d: Date) {
  if (!isOperator) return
  const key = iso(d)
  const e = own.value.get(key)
  dialogDay.value = key
  dialogBusiness.value = e ? e.is_business : d.getDay() === 0 || d.getDay() === 6
  dialogComment.value = e?.comment ?? ''
  dialog.value = true
}

async function load() {
  loading.value = true
  try {
    const days = await api.calendars.days(code.value, year.value)
    own.value = new Map(days.map((d) => [d.day, d]))
    const b = current.value?.base_code
    base.value = b ? new Map((await api.calendars.days(b, year.value)).map((d) => [d.day, d])) : new Map()
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

async function save() {
  try {
    await api.calendars.setDay(code.value, dialogDay.value, dialogBusiness.value, dialogComment.value)
    dialog.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

async function remove(day: string) {
  try {
    await api.calendars.deleteDay(code.value, day)
    await load()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

async function shift() {
  try {
    shiftResult.value = await api.calendars.shift(code.value, calcDate.value, calcDays.value)
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

onMounted(async () => {
  calendars.value = await api.calendars.list()
  await load()
})
</script>

<style scoped>
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
.legend {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
}
.sw {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 2px;
  margin-left: 8px;
}
.sw.holiday {
  background: #f8cbad;
}
.sw.working {
  background: #c6e0b4;
}
.sw.inherited {
  background: #fce4d6;
  border: 1px dashed #e6a23c;
}
.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 420px;
  gap: 12px;
  align-items: start;
}
.months {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}
.month {
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 8px;
}
.month-title {
  font-weight: 600;
  margin-bottom: 4px;
}
.week {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  text-align: center;
  font-size: 12px;
}
.week.head {
  color: var(--el-text-color-secondary);
}
.day {
  padding: 3px 0;
  cursor: pointer;
  border-radius: 3px;
}
.day:hover {
  outline: 1px solid var(--el-color-primary);
}
.weekend {
  color: var(--el-color-danger);
}
.holiday {
  background: #f8cbad;
}
.working {
  background: #c6e0b4;
  color: inherit;
}
.inherited-off {
  background: #fce4d6;
}
.calc {
  display: flex;
  align-items: center;
  gap: 6px;
}
.muted {
  color: var(--el-text-color-secondary);
}
.small {
  font-size: 12px;
}
.mt {
  margin-top: 8px;
}
.mb {
  margin-bottom: 12px;
}
</style>
