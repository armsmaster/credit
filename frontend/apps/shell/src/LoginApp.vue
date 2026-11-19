<template>
  <div class="login">
    <el-card class="login-card" shadow="always">
      <div class="brand"><img src="/favicon.svg" alt="" /> CDS Pricing</div>

      <!-- step 1: credentials -->
      <el-form v-if="step === 'login'" label-position="top" @submit.prevent="submitLogin">
        <h2>Вход</h2>
        <el-form-item label="Логин или email">
          <el-input ref="usernameInput" v-model="username" autocomplete="username" size="large" />
        </el-form-item>
        <el-form-item label="Пароль">
          <el-input v-model="password" type="password" show-password autocomplete="current-password" size="large" />
        </el-form-item>
        <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon class="mb" />
        <el-button type="primary" native-type="submit" size="large" class="wide" :loading="busy" :disabled="!username || !password">
          Войти
        </el-button>
        <p class="hint">Нет пароля? Попросите администратора прислать ссылку для первого входа.</p>
      </el-form>

      <!-- step 2: temporary password must be replaced -->
      <el-form v-else label-position="top" @submit.prevent="submitNewPassword">
        <h2>Первый вход</h2>
        <p class="hint">У учётной записи <b>{{ username }}</b> временный пароль. Задайте свой — не короче 8 символов.</p>
        <el-form-item label="Новый пароль">
          <el-input v-model="newPassword" type="password" show-password autocomplete="new-password" size="large" />
        </el-form-item>
        <el-form-item label="Повторите пароль">
          <el-input v-model="newPassword2" type="password" show-password autocomplete="new-password" size="large" />
        </el-form-item>
        <el-alert v-if="error" type="error" :title="error" :closable="false" show-icon class="mb" />
        <el-button type="primary" native-type="submit" size="large" class="wide" :loading="busy" :disabled="!canChange">
          Сохранить и войти
        </el-button>
        <el-button link class="back" @click="backToLogin">Назад</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'

import { ApiError, firstLogin, login } from '@cds/platform'

const step = ref<'login' | 'change'>('login')
const username = ref('')
const password = ref('')
const newPassword = ref('')
const newPassword2 = ref('')
const error = ref('')
const busy = ref(false)
const usernameInput = ref<{ focus: () => void }>()

const canChange = computed(() => newPassword.value.length >= 8 && newPassword.value === newPassword2.value)

onMounted(() => nextTick(() => usernameInput.value?.focus()))

// After a successful login the session cookie is set: reload opens the requested page.
const enter = () => window.location.reload()

async function submitLogin() {
  busy.value = true
  error.value = ''
  try {
    await login(username.value.trim(), password.value)
    enter()
  } catch (e) {
    if (e instanceof ApiError && e.code === 'password_change_required') {
      step.value = 'change'
    } else {
      error.value = (e as Error).message
    }
  } finally {
    busy.value = false
  }
}

async function submitNewPassword() {
  busy.value = true
  error.value = ''
  try {
    await firstLogin(username.value.trim(), password.value, newPassword.value)
    enter()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

function backToLogin() {
  step.value = 'login'
  password.value = newPassword.value = newPassword2.value = ''
  error.value = ''
}
</script>

<style scoped>
.login {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(160deg, #eef3f9 0%, #f7f8fa 60%);
}
.login-card {
  width: 400px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
  font-size: 18px;
  color: var(--cds-brand);
  margin-bottom: 8px;
}
.brand img {
  width: 28px;
}
h2 {
  margin: 8px 0 16px;
  font-size: 20px;
}
.wide {
  width: 100%;
}
.mb {
  margin-bottom: 12px;
}
.hint {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin: 12px 0 0;
}
.back {
  margin-top: 8px;
}
</style>
