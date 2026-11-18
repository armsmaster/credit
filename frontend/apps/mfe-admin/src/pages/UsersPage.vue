<template>
  <div>
    <div class="page-toolbar">
      <el-input v-model="search" placeholder="Поиск: логин, имя, email" clearable style="width: 300px" @change="load">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <div class="spacer" />
      <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon>&nbsp;Новый пользователь</el-button>
    </div>

    <el-table v-loading="loading" :data="users" stripe>
      <el-table-column prop="username" label="Логин" min-width="140" />
      <el-table-column label="Имя" min-width="180">
        <template #default="{ row }">{{ [row.first_name, row.last_name].filter(Boolean).join(' ') || '—' }}</template>
      </el-table-column>
      <el-table-column prop="email" label="Email" min-width="200" />
      <el-table-column label="Роль" width="190">
        <template #default="{ row }">
          <el-select
            :model-value="row.role"
            size="small"
            placeholder="нет роли"
            :disabled="row.id === me"
            @change="(r: Role) => changeRole(row, r)"
          >
            <el-option v-for="r in roles" :key="r" :value="r" :label="ROLE_TITLES[r]" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="Статус" width="230">
        <template #default="{ row }">
          <el-tag v-if="!row.enabled" type="danger">Заблокирован</el-tag>
          <el-tooltip v-else-if="row.invite_pending" :content="`Ссылка действует до ${formatDateTime(row.invite_expires_at)}`">
            <el-tag type="warning">Ожидает первого входа</el-tag>
          </el-tooltip>
          <el-tag v-else type="success">Активен</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="Создан" width="170">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="" width="250" align="right">
        <template #default="{ row }">
          <el-button size="small" @click="reissue(row)">Ссылка для входа</el-button>
          <el-button v-if="row.enabled" size="small" type="danger" plain :disabled="row.id === me" @click="toggle(row, false)">
            Блокировать
          </el-button>
          <el-button v-else size="small" type="success" plain @click="toggle(row, true)">Разблокировать</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- create -->
    <el-dialog v-model="createOpen" title="Новый пользователь" width="520px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="140px">
        <el-form-item label="Логин" prop="username"><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="Email" prop="email"><el-input v-model="form.email" /></el-form-item>
        <el-form-item label="Имя" prop="first_name"><el-input v-model="form.first_name" /></el-form-item>
        <el-form-item label="Фамилия" prop="last_name"><el-input v-model="form.last_name" /></el-form-item>
        <el-form-item label="Роль" prop="role">
          <el-radio-group v-model="form.role">
            <el-radio-button v-for="r in roles" :key="r" :value="r">{{ ROLE_TITLES[r] }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="Ссылка действует">
          <el-input-number v-model="form.invite_ttl_days" :min="1" :max="90" />&nbsp;дней
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">Отмена</el-button>
        <el-button type="primary" :loading="saving" @click="create">Создать</el-button>
      </template>
    </el-dialog>

    <!-- magic link -->
    <el-dialog v-model="linkOpen" title="Ссылка для первого входа" width="620px">
      <p>
        Передайте ссылку пользователю <b>{{ linkUser }}</b>. По ней он задаст себе пароль.<br />
        <span class="muted">Ссылка одноразовая, действует до {{ formatDateTime(invite?.expires_at) }}. Повторно её показать нельзя — при необходимости выпустите новую.</span>
      </p>
      <el-input :model-value="invite?.link" readonly>
        <template #append>
          <el-button @click="copy"><el-icon><CopyDocument /></el-icon>&nbsp;Копировать</el-button>
        </template>
      </el-input>
      <template #footer><el-button type="primary" @click="linkOpen = false">Готово</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import { formatDateTime, getPlatform, Role, ROLE_TITLES } from '@cds/platform'

import { api, type Invite, type User } from '../api'

const roles = [Role.Viewer, Role.Operator, Role.Admin]
const me = getPlatform().user.id
const users = ref<User[]>([])
const loading = ref(false)
const search = ref('')

const createOpen = ref(false)
const saving = ref(false)
const formRef = ref<FormInstance>()
const form = reactive({ username: '', email: '', first_name: '', last_name: '', role: Role.Viewer, invite_ttl_days: 7 })
const rules: FormRules = {
  username: [
    { required: true, message: 'Укажите логин' },
    { pattern: /^[a-zA-Z0-9._-]{3,64}$/, message: 'Латиница, цифры, . _ -, от 3 символов' },
  ],
  email: [{ required: true, type: 'email', message: 'Укажите корректный email' }],
  first_name: [{ required: true, message: 'Укажите имя' }],
  last_name: [{ required: true, message: 'Укажите фамилию' }],
}

const linkOpen = ref(false)
const invite = ref<Invite | null>(null)
const linkUser = ref('')

async function load() {
  loading.value = true
  try {
    users.value = await api.users.list(search.value || undefined)
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  Object.assign(form, { username: '', email: '', first_name: '', last_name: '', role: Role.Viewer, invite_ttl_days: 7 })
  createOpen.value = true
}

async function create() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  saving.value = true
  try {
    const res = await api.users.create({ ...form })
    createOpen.value = false
    showLink(res.user.username, res.invite)
    await load()
  } catch (e: any) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

function showLink(username: string, inv: Invite) {
  linkUser.value = username
  invite.value = inv
  linkOpen.value = true
}

async function copy() {
  if (!invite.value) return
  try {
    await navigator.clipboard.writeText(invite.value.link)
    ElMessage.success('Ссылка скопирована')
  } catch {
    ElMessage.warning('Скопируйте ссылку вручную')
  }
}

async function changeRole(user: User, role: Role) {
  try {
    await ElMessageBox.confirm(`Назначить пользователю ${user.username} роль «${ROLE_TITLES[role]}»?`, 'Смена роли', { type: 'warning' })
    await api.users.setRole(user.id, role)
    ElMessage.success('Роль изменена')
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error(e.message)
  }
  await load()
}

async function toggle(user: User, enabled: boolean) {
  try {
    if (!enabled) {
      await ElMessageBox.confirm(`Заблокировать ${user.username}? Активные сессии будут завершены.`, 'Блокировка', { type: 'warning' })
    }
    await api.users.setEnabled(user.id, enabled)
    ElMessage.success(enabled ? 'Пользователь разблокирован' : 'Пользователь заблокирован')
    await load()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error(e.message)
  }
}

async function reissue(user: User) {
  try {
    await ElMessageBox.confirm(
      `Выпустить новую ссылку для ${user.username}? Предыдущая ссылка перестанет работать. По ссылке пользователь сможет задать новый пароль.`,
      'Ссылка для входа',
      { type: 'info' },
    )
    showLink(user.username, await api.users.invite(user.id))
    await load()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error(e.message)
  }
}

onMounted(load)
</script>

<style scoped>
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>
