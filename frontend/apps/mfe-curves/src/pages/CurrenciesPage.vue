<template>
  <div>
    <div class="page-toolbar">
      <div class="spacer" />
      <el-button v-if="isOperator" type="primary" @click="openEdit()"><el-icon><Plus /></el-icon>&nbsp;Добавить валюту</el-button>
    </div>
    <el-table v-loading="loading" :data="items" stripe style="max-width: 900px">
      <el-table-column prop="code" label="Код" width="90" />
      <el-table-column prop="numeric_code" label="Цифровой" width="100" />
      <el-table-column prop="name" label="Название" />
      <el-table-column label="Статус" width="120">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? 'активна' : 'неактивна' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="isOperator" width="120" align="right">
        <template #default="{ row }"><el-button size="small" @click="openEdit(row)">Изменить</el-button></template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="open" :title="editing ? `Валюта ${editing.code}` : 'Новая валюта'" width="460px">
      <el-form label-width="130px">
        <el-form-item label="Код ISO 4217"><el-input v-model="form.code" :disabled="!!editing" maxlength="3" placeholder="RUB" /></el-form-item>
        <el-form-item label="Цифровой код"><el-input v-model="form.numeric_code" maxlength="3" placeholder="643" /></el-form-item>
        <el-form-item label="Название"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="Активна"><el-switch v-model="form.is_active" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="open = false">Отмена</el-button>
        <el-button type="primary" @click="save">Сохранить</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import { getPlatform, Role } from '@cds/platform'

import { api } from '../api'
import type { Currency } from '../types'

const isOperator = getPlatform().hasRole(Role.Operator)
const items = ref<Currency[]>([])
const loading = ref(false)
const open = ref(false)
const editing = ref<Currency | null>(null)
const form = reactive({ code: '', name: '', numeric_code: '' as string | null, is_active: true })

async function load() {
  loading.value = true
  try {
    items.value = await api.currencies.list()
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function openEdit(c?: Currency) {
  editing.value = c ?? null
  Object.assign(form, c ? { code: c.code, name: c.name, numeric_code: c.numeric_code, is_active: c.is_active } : { code: '', name: '', numeric_code: '', is_active: true })
  open.value = true
}

async function save() {
  const body = { ...form, code: form.code.toUpperCase(), numeric_code: form.numeric_code || null }
  try {
    if (editing.value) await api.currencies.update(editing.value.code, body)
    else await api.currencies.create(body)
    open.value = false
    ElMessage.success('Сохранено')
    await load()
  } catch (e: any) {
    ElMessage.error(e.message)
  }
}

onMounted(load)
</script>
