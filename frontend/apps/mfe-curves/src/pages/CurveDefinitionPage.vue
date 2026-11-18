<template>
  <div class="def-page">
    <h2>{{ isNew ? 'Новая кривая' : `Параметры кривой ${code}` }}</h2>
    <el-form ref="formRef" :model="form" :rules="rules" label-width="260px" class="form" v-loading="loading">
      <el-form-item label="Кодовое имя" prop="code">
        <el-input v-model="form.code" :disabled="!isNew" placeholder="RUB-RUONIA-OIS" />
      </el-form-item>
      <el-form-item label="Название" prop="name"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="Валюта" prop="currency_code">
        <el-select v-model="form.currency_code" style="width: 200px">
          <el-option v-for="c in currencies" :key="c.code" :value="c.code" :label="`${c.code} — ${c.name}`" :disabled="!c.is_active" />
        </el-select>
      </el-form-item>
      <el-form-item label="Индекс (плавающая нога)"><el-input v-model="form.index_name" placeholder="RUONIA / RUSFAR / ключевая ставка" /></el-form-item>
      <el-form-item label="Описание"><el-input v-model="form.description" type="textarea" :rows="2" /></el-form-item>

      <el-divider content-position="left">Конвенции котируемых свопов</el-divider>
      <el-form-item label="Базис расчёта дней">
        <el-radio-group v-model="form.day_count">
          <el-radio-button value="ACT/365F">ACT/365F</el-radio-button>
          <el-radio-button value="ACT/360">ACT/360</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="Одна выплата при сроке до, мес.">
        <el-input-number v-model="form.single_payment_max_months" :min="0" :max="24" />
      </el-form-item>
      <el-form-item label="Периодичность фиксированной ноги">
        <el-select v-model="form.fixed_frequency_months" style="width: 200px">
          <el-option :value="12" label="ежегодно (12M)" />
          <el-option :value="6" label="раз в полгода (6M)" />
          <el-option :value="3" label="ежеквартально (3M)" />
          <el-option :value="1" label="ежемесячно (1M)" />
        </el-select>
      </el-form-item>

      <el-divider content-position="left">Параметры расчёта по умолчанию (оператор может переопределить)</el-divider>
      <div class="params">
        <ParamsForm v-if="params.smoothness !== undefined" v-model="params" :defaults="systemDefaults" />
      </div>

      <el-form-item label="Активна"><el-switch v-model="form.is_active" /></el-form-item>
      <el-form-item>
        <el-button @click="router.back()">Отмена</el-button>
        <el-button type="primary" :loading="saving" @click="save">Сохранить</el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '../api'
import ParamsForm from '../components/ParamsForm.vue'
import type { Currency, Params } from '../types'

const route = useRoute()
const router = useRouter()
const code = computed(() => (route.params.code as string | undefined) ?? '')
const isNew = computed(() => !code.value)

const formRef = ref<FormInstance>()
const loading = ref(false)
const saving = ref(false)
const currencies = ref<Currency[]>([])
const systemDefaults = ref<Params>({} as Params)
const params = ref<Params>({} as Params)
const form = reactive({
  code: '',
  name: '',
  currency_code: 'RUB',
  index_name: '',
  description: '',
  day_count: 'ACT/365F' as 'ACT/365F' | 'ACT/360',
  fixed_frequency_months: 12,
  single_payment_max_months: 12,
  is_active: true,
})
const rules: FormRules = {
  code: [
    { required: true, message: 'Укажите кодовое имя' },
    { pattern: /^[A-Z0-9][A-Z0-9_.-]{1,63}$/, message: 'Заглавные латинские буквы, цифры, «-», «_», «.»' },
  ],
  name: [{ required: true, message: 'Укажите название' }],
  currency_code: [{ required: true, message: 'Выберите валюту' }],
}

onMounted(async () => {
  loading.value = true
  try {
    const [cur, meta] = await Promise.all([api.currencies.list(), api.meta()])
    currencies.value = cur
    systemDefaults.value = meta.default_params
    params.value = { ...meta.default_params }
    if (!isNew.value) {
      const d = await api.definitions.get(code.value)
      Object.assign(form, {
        code: d.code,
        name: d.name,
        currency_code: d.currency_code,
        index_name: d.index_name,
        description: d.description,
        day_count: d.day_count,
        fixed_frequency_months: d.fixed_frequency_months,
        single_payment_max_months: d.single_payment_max_months,
        is_active: d.is_active,
      })
      params.value = { ...d.effective_params }
    }
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
})

async function save() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  saving.value = true
  // store only values that differ from the system defaults: system-wide improvements stay effective
  const overrides = Object.fromEntries(
    Object.entries(params.value).filter(([k, v]) => v !== (systemDefaults.value as any)[k]),
  )
  const body = { ...form, default_params: overrides }
  try {
    const saved = isNew.value ? await api.definitions.create(body) : await api.definitions.update(code.value, body)
    ElMessage.success('Сохранено')
    router.push(`/curves/${encodeURIComponent(saved.code)}`)
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.def-page h2 {
  margin-top: 0;
}
.form {
  max-width: 900px;
  background: #fff;
  padding: 20px;
  border-radius: 6px;
}
.params {
  padding: 0 0 12px 260px;
}
</style>
