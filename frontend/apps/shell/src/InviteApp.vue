<template>
  <div class="invite">
    <el-card class="invite-card">
      <template #header>
        <div class="title"><img src="/favicon.svg" alt="" /> CDS Pricing — первый вход</div>
      </template>
      <div v-if="loading">Проверяем ссылку…</div>
      <el-result v-else-if="error" icon="error" title="Ссылка недействительна" :sub-title="error" />
      <template v-else-if="info && info.status !== 'valid' && !done">
        <el-result icon="warning" :title="statusTitle" sub-title="Попросите администратора выпустить новую ссылку." />
      </template>
      <template v-else-if="done">
        <el-result icon="success" title="Пароль установлен" :sub-title="`Ваш логин: ${info?.username}`">
          <template #extra>
            <el-button type="primary" @click="goLogin">Перейти в систему</el-button>
          </template>
        </el-result>
      </template>
      <el-form v-else-if="info" label-position="top" @submit.prevent="submit">
        <p>
          Логин: <b>{{ info.username }}</b><br />
          <span class="muted">Ссылка действует до {{ formatDateTime(info.expires_at) }}</span>
        </p>
        <el-form-item label="Новый пароль (не короче 8 символов)">
          <el-input v-model="password" type="password" show-password autocomplete="new-password" />
        </el-form-item>
        <el-form-item label="Повторите пароль">
          <el-input v-model="password2" type="password" show-password autocomplete="new-password" />
        </el-form-item>
        <el-alert v-if="submitError" type="error" :title="submitError" :closable="false" show-icon class="mb" />
        <el-button type="primary" native-type="submit" :loading="submitting" :disabled="!canSubmit">Задать пароль</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { createHttp, formatDateTime } from '@cds/platform'

interface InvitationInfo {
  username: string
  status: 'valid' | 'expired' | 'used' | 'revoked'
  expires_at: string
}

const http = createHttp()
const token = window.location.pathname.split('/invite/')[1]?.replace(/\/$/, '') ?? ''
const loading = ref(true)
const error = ref('')
const info = ref<InvitationInfo | null>(null)
const password = ref('')
const password2 = ref('')
const submitting = ref(false)
const submitError = ref('')
const done = ref(false)

const canSubmit = computed(() => password.value.length >= 8 && password.value === password2.value)
const statusTitle = computed(
  () => ({ expired: 'Срок действия ссылки истёк', used: 'Ссылка уже использована', revoked: 'Ссылка отозвана', valid: '' })[info.value?.status ?? 'valid'],
)

onMounted(async () => {
  try {
    info.value = await http.get<InvitationInfo>(`/api/admin/public/invitations/${encodeURIComponent(token)}`)
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

async function submit() {
  submitting.value = true
  submitError.value = ''
  try {
    await http.post(`/api/admin/public/invitations/${encodeURIComponent(token)}/accept`, { password: password.value })
    done.value = true
  } catch (e: any) {
    submitError.value = e.message
  } finally {
    submitting.value = false
  }
}

function goLogin() {
  window.location.href = '/'
}
</script>

<style scoped>
.invite {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
}
.invite-card {
  width: 440px;
}
.title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
}
.title img {
  width: 24px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.mb {
  margin-bottom: 12px;
}
</style>
