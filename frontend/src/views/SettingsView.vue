<template>
  <div class="settings-view">
    <header class="header">
      <div class="header-left">
        <button class="back-btn" @click="router.push('/dashboard')" title="返回">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20">
            <polyline points="15 18 9 12 15 6"/>
          </svg>
        </button>
        <div class="header-title">
          <h1>系统设置</h1>
        </div>
      </div>
      <div class="header-right">
        <button class="header-btn logout" @click="handleLogout">退出</button>
      </div>
    </header>

    <div class="settings-grid">
      <div class="settings-card">
        <h3>外观</h3>
        <div class="setting-item">
          <div class="setting-info">
            <span class="setting-label">主题模式</span>
            <span class="setting-desc">切换深色/浅色主题</span>
          </div>
          <button class="theme-toggle" @click="toggleTheme">
            <span class="theme-icon">{{ isDark ? '' : '☀️' }}</span>
            <span>{{ isDark ? '深色' : '浅色' }}</span>
          </button>
        </div>
      </div>

      <div class="settings-card">
        <h3>快捷键</h3>
        <div class="shortcuts-list">
          <div class="shortcut-item">
            <span class="shortcut-keys"><kbd>Ctrl</kbd>+<kbd>K</kbd></span>
            <span class="shortcut-desc">聚焦搜索框</span>
          </div>
          <div class="shortcut-item">
            <span class="shortcut-keys"><kbd>Space</kbd></span>
            <span class="shortcut-desc">全选/取消全选</span>
          </div>
          <div class="shortcut-item">
            <span class="shortcut-keys"><kbd>Delete</kbd></span>
            <span class="shortcut-desc">批量删除选中设备</span>
          </div>
          <div class="shortcut-item">
            <span class="shortcut-keys"><kbd>R</kbd></span>
            <span class="shortcut-desc">刷新设备列表</span>
          </div>
          <div class="shortcut-item">
            <span class="shortcut-keys"><kbd>Esc</kbd></span>
            <span class="shortcut-desc">关闭弹窗</span>
          </div>
        </div>
      </div>

      <div class="settings-card">
        <h3>关于</h3>
        <div class="about-info">
          <p><strong>绿邮群控系统</strong></p>
          <p>版本：v5.2.0</p>
          <p>技术栈：Vue 3 + Naive UI + Pinia</p>
          <p>License: MIT</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore, useDevicesStore } from '../stores'
import { useTheme } from '../composables/useTheme'
import { useDeviceActions } from '../composables/useDeviceActions'

const router = useRouter()
const authStore = useAuthStore()
const devicesStore = useDevicesStore()

const { isDark, toggleTheme } = useTheme()
const { refresh } = useDeviceActions()

async function handleLogout() {
  await authStore.logout(true)
  router.push('/login')
}

onMounted(async () => {
  if (!(await authStore.restore())) {
    router.push('/login')
  }
})
</script>

<style scoped>
.settings-view { padding: 24px; max-width: 1440px; margin: 0 auto; }
.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 20px; border-bottom: 1px solid var(--border); }
.header-left { display: flex; align-items: center; gap: 12px; }
.header-right { display: flex; align-items: center; gap: 8px; }
.header-title h1 { font-size: 20px; font-weight: 700; letter-spacing: -0.02em; }
.back-btn {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-primary);
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--transition);
}
.back-btn:hover {
  background: var(--bg-card-hover);
  border-color: var(--border-light);
  transform: translateX(-2px);
}
.header-btn {
  background: var(--bg-card);
  border: 1px solid var(--border);
  color: var(--text-primary);
  padding: 8px 18px;
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  transition: all var(--transition);
}
.header-btn.logout:hover { background: var(--danger-glow); }
.page-title { font-size: 24px; font-weight: 700; margin-bottom: 24px; letter-spacing: -0.02em; }
.settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; }
.settings-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 24px;
}
.settings-card h3 { font-size: 16px; font-weight: 600; margin-bottom: 18px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
.setting-item { display: flex; justify-content: space-between; align-items: center; }
.setting-info { display: flex; flex-direction: column; gap: 4px; }
.setting-label { font-size: 14px; font-weight: 500; }
.setting-desc { font-size: 12px; color: var(--text-muted); }
.theme-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 10px 18px;
  color: var(--text-primary);
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  transition: all var(--transition);
}
.theme-toggle:hover {
  border-color: var(--primary);
  background: var(--bg-card-hover);
}
.theme-icon { font-size: 18px; }
.shortcuts-list { display: flex; flex-direction: column; gap: 12px; }
.shortcut-item { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; }
.shortcut-keys { display: flex; gap: 4px; align-items: center; }
.shortcut-keys kbd {
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 3px 8px;
  font-size: 12px;
  font-family: 'JetBrains Mono', monospace;
  color: var(--text-primary);
}
.shortcut-desc { font-size: 13px; color: var(--text-secondary); }
.about-info { display: flex; flex-direction: column; gap: 8px; font-size: 14px; color: var(--text-secondary); }
.about-info strong { color: var(--text-primary); font-size: 16px; }
</style>
