<template>
  <div v-loading="loading">
    <div class="page-toolbar">
      <h2>{{ isNew ? 'Новая кредитная кривая' : `Параметры кривой ${code}` }}</h2>
      <div class="spacer" />
      <el-button @click="back">Отмена</el-button>
      <el-button type="primary" :loading="saving" @click="save">Сохранить</el-button>
    </div>

    <div class="layout">
      <el-card shadow="never">
        <template #header><b>Ключевые атрибуты</b></template>
        <el-form label-position="top" size="default">
          <el-form-item label="Код кривой" required>
            <el-input v-model="form.code" :disabled="!isNew" placeholder="Например, VEB-SENIOR" />
          </el-form-item>
          <el-form-item label="Название" required>
            <el-input v-model="form.name" />
          </el-form-item>
          <el-form-item label="Эмитент (справочник ISS)" required>
            <el-select v-model="form.issuer_id" filterable placeholder="Выберите эмитента" style="width: 100%" @change="loadBonds">
              <el-option v-for="i in issuers" :key="i.emitter_id" :value="i.emitter_id" :label="i.short_title || i.title">
                <span>{{ i.short_title || i.title }}</span>
                <span class="opt-muted">ИНН {{ i.inn }} · подходит {{ i.bonds_eligible }}</span>
              </el-option>
            </el-select>
            <div class="hint">Нет нужного? <router-link to="/credit/issuers">Добавьте эмитента</router-link> — его облигации сразу просканируются.</div>
          </el-form-item>
          <el-form-item label="Кривая дисконтирования (безрисковая)" required>
            <el-select v-model="form.discount_curve_code" filterable style="width: 100%">
              <el-option v-for="c in rateCurves" :key="c.code" :value="c.code" :label="`${c.code} — ${c.name}`" />
            </el-select>
          </el-form-item>
          <el-form-item label="Описание">
            <el-input v-model="form.description" type="textarea" :rows="2" />
          </el-form-item>
          <el-form-item>
            <el-checkbox v-model="form.is_active">Активна</el-checkbox>
          </el-form-item>
        </el-form>
        <el-divider>Параметры расчёта по умолчанию</el-divider>
        <ParamsForm v-model="params" :defaults="meta?.default_params ?? params" />
      </el-card>

      <el-card shadow="never">
        <template #header>
          <div class="card-head">
            <b>Состав кривой: {{ selected.size }} бумаг</b>
            <div class="spacer" />
            <el-checkbox v-model="onlyEligible">Только подходящие</el-checkbox>
            <el-button size="small" :disabled="!recommended.length" @click="takeRecommended">Выбрать рекомендованные ({{ recommended.length }})</el-button>
            <el-button size="small" :disabled="!selectedShort.length" @click="dropShort">Убрать короткие ({{ selectedShort.length }})</el-button>
          </div>
        </template>
        <el-alert v-if="!form.issuer_id" type="info" :closable="false" title="Выберите эмитента — появятся его облигации с рекомендациями." />
        <template v-else>
          <el-alert v-if="selectedProblems.length" type="warning" :closable="false" show-icon class="mb" title="В составе есть бумаги с замечаниями">
            <div v-for="p in selectedProblems" :key="p">• {{ p }}</div>
          </el-alert>
          <BondsTable v-model:selected="selected" v-model:weights="weights" :bonds="visibleBonds" selectable :editable="true" :show-curves="false"
            max-height="620" @open="(b) => (openSecid = b.secid)" @edit="(b) => (editing = b)" />
        </template>
      </el-card>
    </div>

    <BondDrawer :secid="openSecid" editable :version="drawerVersion" @close="openSecid = null" @edit="(b) => (editing = b)" />
    <BondEditDialog :bond="editing" @close="editing = null" @saved="onSaved" />
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '../api'
import BondDrawer from '../components/BondDrawer.vue'
import BondEditDialog from '../components/BondEditDialog.vue'
import BondsTable from '../components/BondsTable.vue'
import ParamsForm from '../components/ParamsForm.vue'
import type { Bond, Issuer, Meta, Params, RateCurveDef } from '../types'

const route = useRoute()
const router = useRouter()
const code = computed(() => (route.params.code ? String(route.params.code) : ''))
const isNew = computed(() => !code.value)

const loading = ref(false)
const saving = ref(false)
const meta = ref<Meta | null>(null)
const issuers = ref<Issuer[]>([])
const rateCurves = ref<RateCurveDef[]>([])
const bonds = ref<Bond[]>([])
const onlyEligible = ref(true)
const selected = ref<Set<string>>(new Set())
const weights = ref<Record<string, number>>({})
const openSecid = ref<string | null>(null)
const editing = ref<Bond | null>(null)
const drawerVersion = ref(0)
const form = ref({ code: '', name: '', issuer_id: null as number | null, discount_curve_code: 'RUB-RUONIA-OIS', description: '', is_active: true })
const params = ref<Params>({ recovery_rate: 0.4, smoothness: 100, long_end_flatness: 100, long_end_flat_from_months: null, secondary_weight: 0.25, short_bond_years: 0.5 })

const visibleBonds = computed(() => bonds.value.filter((b) => !onlyEligible.value || b.profile.eligible || selected.value.has(b.secid)))
const recommended = computed(() => bonds.value.filter((b) => b.recommended))
const selectedBonds = computed(() => bonds.value.filter((b) => selected.value.has(b.secid)))
const selectedShort = computed(() => selectedBonds.value.filter((b) => b.short))
const selectedProblems = computed(() =>
  selectedBonds.value.flatMap((b) => {
    const out: string[] = []
    if (!b.profile.eligible) out.push(`${b.shortname}: ${b.profile.ineligible.join('; ')}`)
    else if (!b.profile.complete) out.push(`${b.shortname}: не хватает данных — ${b.profile.missing.join('; ')}`)
    if (b.short) out.push(`${b.shortname}: до погашения/оферты ${b.years_to_maturity} г. — рекомендуется исключить`)
    return out
  }),
)

async function loadBonds() {
  if (!form.value.issuer_id) {
    bonds.value = []
    return
  }
  bonds.value = await api.bonds.list({ issuer_id: form.value.issuer_id, short_years: params.value.short_bond_years })
}

watch(() => params.value.short_bond_years, () => loadBonds())

function takeRecommended() {
  const next = new Set(selected.value)
  for (const b of recommended.value) {
    next.add(b.secid)
    if (!weights.value[b.secid]) weights.value[b.secid] = 1
  }
  selected.value = next
}

function dropShort() {
  const next = new Set(selected.value)
  for (const b of selectedShort.value) next.delete(b.secid)
  selected.value = next
}

function onSaved() {
  drawerVersion.value++
  loadBonds()
}

function back() {
  router.push(isNew.value ? '/credit' : `/credit/${encodeURIComponent(code.value)}`)
}

function paramOverrides(): Partial<Params> {
  const d = meta.value!.default_params
  const out: Partial<Params> = {}
  for (const k of Object.keys(params.value) as (keyof Params)[]) {
    if (params.value[k] !== d[k] || k === 'recovery_rate') (out as any)[k] = params.value[k]
  }
  return out
}

async function save() {
  if (!form.value.issuer_id) return ElMessage.warning('Выберите эмитента')
  saving.value = true
  const body = {
    ...form.value,
    default_params: paramOverrides(),
    bonds: [...selected.value].map((secid) => ({ secid, weight: weights.value[secid] ?? 1 })),
  }
  try {
    const d = isNew.value ? await api.definitions.create(body) : await api.definitions.update(code.value, body)
    ElMessage.success('Кривая сохранена')
    router.push(`/credit/${encodeURIComponent(d.code)}`)
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  loading.value = true
  try {
    const [m, iss, curves] = await Promise.all([api.meta(), api.issuers.list(), api.rateCurves().catch(() => [])])
    meta.value = m
    issuers.value = iss
    rateCurves.value = curves
    params.value = { ...m.default_params }
    if (!isNew.value) {
      const d = await api.definitions.get(code.value)
      form.value = { code: d.code, name: d.name, issuer_id: d.issuer_id, discount_curve_code: d.discount_curve_code, description: d.description, is_active: d.is_active }
      params.value = { ...d.effective_params }
      selected.value = new Set(d.bonds.map((b) => b.secid))
      weights.value = Object.fromEntries(d.bonds.map((b) => [b.secid, b.weight]))
    } else if (route.query.issuer) {
      form.value.issuer_id = Number(route.query.issuer)
    }
    await loadBonds()
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
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.page-toolbar h2 {
  margin: 0;
}
.spacer {
  flex: 1;
}
.layout {
  display: grid;
  grid-template-columns: 380px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.opt-muted {
  float: right;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-left: 12px;
}
.mb {
  margin-bottom: 12px;
}
</style>
