<template>
  <div>
    <div class="page-toolbar">
      <el-input v-model="search" placeholder="Поиск по коду, названию, эмитенту" clearable style="width: 300px">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <div class="spacer" />
      <router-link v-if="isOperator" to="/credit/new">
        <el-button type="primary"><el-icon><Plus /></el-icon>&nbsp;Новая кредитная кривая</el-button>
      </router-link>
    </div>
    <el-table v-loading="loading" :data="filtered" stripe @row-click="(r: Definition) => router.push(`/credit/${encodeURIComponent(r.code)}`)">
      <el-table-column label="Код" min-width="150">
        <template #default="{ row }"><b>{{ row.code }}</b></template>
      </el-table-column>
      <el-table-column prop="name" label="Название" min-width="200" />
      <el-table-column prop="issuer_title" label="Эмитент" min-width="180" />
      <el-table-column label="Recovery" width="95" align="right">
        <template #default="{ row }">{{ Math.round(row.effective_params.recovery_rate * 100) }}%</template>
      </el-table-column>
      <el-table-column prop="discount_curve_code" label="Дисконтирование" min-width="150" />
      <el-table-column label="Бумаг" width="80" align="right">
        <template #default="{ row }">{{ row.bonds.length }}</template>
      </el-table-column>
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
      <el-table-column label="" width="90">
        <template #default="{ row }"><el-tag v-if="!row.is_active" type="info" size="small">неактивна</el-tag></template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !items.length" description="Кредитных кривых пока нет">
      <p class="muted">Добавьте эмитента на странице «Эмитенты», дождитесь скана его облигаций и создайте кривую.</p>
    </el-empty>
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
const today = ref('')

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return items.value.filter((d) => !q || `${d.code} ${d.name} ${d.issuer_title}`.toLowerCase().includes(q))
})

onMounted(async () => {
  loading.value = true
  try {
    const [list, meta] = await Promise.all([api.definitions.list(), api.meta()])
    items.value = list
    today.value = meta.today
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.page-toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
}
.spacer {
  flex: 1;
}
.muted {
  color: var(--el-text-color-secondary);
}
:deep(.el-table__row) {
  cursor: pointer;
}
</style>
