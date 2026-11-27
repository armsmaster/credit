<template>
  <div>
    <div class="page-toolbar">
      <el-select v-model="currency" placeholder="Валюта" clearable style="width: 140px">
        <el-option v-for="c in currencies" :key="c" :value="c" />
      </el-select>
      <el-input v-model="search" placeholder="Поиск по коду или названию" clearable style="width: 280px">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <div class="spacer" />
      <el-tooltip content="Открытый API (без авторизации, только чтение): модельные миды свопов на котируемых сроках — онлайн и за дату. Описание и проверка запросов — в Swagger">
        <el-button tag="a" href="/api/curves/public/docs" target="_blank" rel="noopener"><el-icon><Link /></el-icon>&nbsp;API модельных цен</el-button>
      </el-tooltip>
      <router-link v-if="isOperator" to="/curves/new">
        <el-button type="primary"><el-icon><Plus /></el-icon>&nbsp;Новая кривая</el-button>
      </router-link>
    </div>
    <el-table v-loading="loading" :data="filtered" stripe @row-click="open">
      <el-table-column label="Код" min-width="170">
        <template #default="{ row }"><b class="code">{{ row.code }}</b></template>
      </el-table-column>
      <el-table-column prop="name" label="Название" min-width="220" />
      <el-table-column prop="currency_code" label="Валюта" width="90" />
      <el-table-column prop="index_name" label="Индекс" min-width="160" />
      <el-table-column label="Текущая кривая" width="190">
        <template #default="{ row }">
          <template v-if="row.current_state">
            {{ formatDate(row.current_state.as_of_date) }}
            <el-tag v-if="row.current_state.as_of_date === today" size="small" type="success">сегодня</el-tag>
          </template>
          <span v-else class="muted">не построена</span>
        </template>
      </el-table-column>
      <el-table-column label="В истории по" width="130">
        <template #default="{ row }">{{ formatDate(row.last_history_date) }}</template>
      </el-table-column>
      <el-table-column label="" width="80">
        <template #default="{ row }"><el-tag v-if="!row.is_active" type="info" size="small">неактивна</el-tag></template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { formatDate, getPlatform, Role } from '@cds/platform'

import { api } from '../api'
import type { Definition } from '../types'

const router = useRouter()
const isOperator = getPlatform().hasRole(Role.Operator)
const items = ref<Definition[]>([])
const loading = ref(false)
const search = ref('')
const currency = ref('')
const today = ref('')

const currencies = computed(() => [...new Set(items.value.map((d) => d.currency_code))].sort())
const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return items.value.filter(
    (d) => (!currency.value || d.currency_code === currency.value) && (!q || d.code.toLowerCase().includes(q) || d.name.toLowerCase().includes(q)),
  )
})

function open(row: Definition) {
  router.push(`/curves/${encodeURIComponent(row.code)}`)
}

onMounted(async () => {
  loading.value = true
  try {
    const [defs, meta] = await Promise.all([api.definitions.list(), api.meta()])
    items.value = defs
    today.value = meta.today
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.code {
  color: var(--el-color-primary);
  cursor: pointer;
}
.muted {
  color: var(--el-text-color-secondary);
}
:deep(.el-table__row) {
  cursor: pointer;
}
</style>
