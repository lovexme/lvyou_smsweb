<template>
  <header class="header">
    <div class="header-left">
      <div class="logo" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8z" />
          <path d="M12 6a2 2 0 100 4 2 2 0 000-4zm-4 8a4 4 0 118 0v1H8v-1z" />
        </svg>
      </div>
      <div class="header-title">
        <h1>控制台</h1>
        <p>管理中心</p>
      </div>
    </div>
    <div class="header-right">
      <button class="header-btn icon-btn" @click="emit('toggle-theme')" :title="isDark ? '切换到浅色模式' : '切换到深色模式'">
        <svg v-if="isDark" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
          <circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
        </svg>
        <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
          <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/>
        </svg>
      </button>
      <button class="header-btn icon-btn" @click="emit('open-settings')" title="设置">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
          <circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z"/>
        </svg>
      </button>
      <button class="header-btn primary" :disabled="scanning" @click="emit('scan')">
        <span v-if="scanning" class="spinner" style="width:14px;height:14px;border-width:2px;margin-right:6px;vertical-align:middle;"></span>
        {{ scanning ? '扫描中...' : '扫描' }}
      </button>
      <button class="header-btn" :disabled="loading" @click="emit('refresh')">刷新</button>
      <button class="header-btn logout" @click="emit('logout')">退出</button>
    </div>
  </header>
</template>

<script setup>
defineProps({
  loading: { type: Boolean, default: false },
  scanning: { type: Boolean, default: false },
  isDark: { type: Boolean, default: true }
})

const emit = defineEmits(['scan', 'refresh', 'logout', 'toggle-theme', 'open-settings'])
</script>
