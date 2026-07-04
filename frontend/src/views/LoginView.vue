<template>
  <div class="login-container">
    <div class="login-box">
      <div class="login-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8z" />
          <path d="M12 6a2 2 0 100 4 2 2 0 000-4zm-4 8a4 4 0 118 0v1H8v-1z" />
        </svg>
      </div>
      <h1 class="login-title">控制台</h1>
      <p class="login-subtitle">请输入密码登录系统</p>
      <div class="login-form">
        <input
          v-model="password"
          class="login-input"
          type="password"
          placeholder="请输入密码"
          autocomplete="current-password"
          @keyup.enter="handleLogin"
        />
        <button class="login-button" :disabled="loading" @click="handleLogin">
          <span v-if="loading" class="login-loading"><span class="spinner"></span> 验证中...</span>
          <span v-else>登 录</span>
        </button>
      </div>
      <div v-if="notice.text" class="login-notice" :class="'notice-' + notice.type">
        {{ notice.text }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore, useDevicesStore, useNoticeStore } from '../stores'
import { storeToRefs } from 'pinia'

const router = useRouter()
const authStore = useAuthStore()
const devicesStore = useDevicesStore()
const noticeStore = useNoticeStore()

const { uiPass } = storeToRefs(authStore)
const { text: noticeText, type: noticeType } = storeToRefs(noticeStore)

const password = ref('')
const loading = ref(false)

const notice = {
  get text() { return noticeText.value },
  get type() { return noticeType.value }
}

async function handleLogin() {
  loading.value = true
  try {
    uiPass.value = password.value
    const ok = await authStore.login()
    if (ok) {
      await devicesStore.refresh()
      router.push('/dashboard')
    }
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-container {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 20px;
  background: radial-gradient(ellipse at 50% 0%, rgba(59,130,246,0.08) 0%, transparent 60%);
}
.login-box {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  padding: 44px 40px;
  width: 100%;
  max-width: 400px;
  text-align: center;
  box-shadow: var(--shadow-lg);
  animation: slideUp 0.4s ease-out;
}
@keyframes slideUp {
  from { opacity: 0; transform: translateY(16px) scale(0.97); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.login-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 72px;
  height: 72px;
  margin: 0 auto 20px;
  background: linear-gradient(135deg, rgba(59,130,246,0.15), rgba(59,130,246,0.05));
  border-radius: var(--radius-lg);
  color: var(--primary);
  animation: float 3s ease-in-out infinite;
}
@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-6px); }
}
.login-icon svg { width: 48px; height: 48px; }
.login-title { font-size: 24px; font-weight: 700; margin-bottom: 6px; letter-spacing: -0.02em; }
.login-subtitle { color: var(--text-muted); font-size: 14px; margin-bottom: 28px; }
.login-form { display: flex; flex-direction: column; gap: 14px; }
.login-input {
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 14px 16px;
  font-size: 15px;
  color: var(--text-primary);
  outline: none;
  transition: border-color var(--transition), box-shadow var(--transition);
}
.login-input:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-glow);
}
.login-button {
  background: linear-gradient(135deg, var(--primary), var(--primary-dark));
  color: white;
  border: none;
  border-radius: var(--radius-md);
  padding: 14px;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: transform var(--transition), box-shadow var(--transition);
}
.login-button:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: var(--shadow-glow);
}
.login-button:active:not(:disabled) { transform: translateY(0); }
.login-button:disabled { opacity: 0.5; cursor: not-allowed; }
.login-loading { display: inline-flex; align-items: center; gap: 8px; }
.login-notice { margin-top: 16px; padding: 10px 14px; border-radius: var(--radius-md); font-size: 13px; animation: fadeIn 0.3s ease-out; }
@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
.notice-ok { background: var(--success-glow); color: var(--success); }
.notice-err { background: var(--danger-glow); color: var(--danger); }
.notice-info { background: var(--primary-glow); color: var(--primary); }
.notice-warn { background: var(--warning-glow); color: var(--warning); }
</style>
