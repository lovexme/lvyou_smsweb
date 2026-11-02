<template>
  <div class="messages-view">
    <AppHeader
      :loading="loading"
      :scanning="false"
      :is-dark="isDark"
      @refresh="load"
      @logout="handleLogout"
      @toggle-theme="toggleTheme"
      @open-settings="$router.push('/settings')"
      @scan="$router.push('/dashboard')"
    />

    <div class="toolbar">
      <div class="toolbar-left">
        <select v-model="deviceId" class="filter-select" @change="reload">
          <option :value="0">全部设备</option>
          <option v-for="d in devices" :key="d.id" :value="d.id">{{ d.alias || d.devId || d.ip }}</option>
        </select>
        <select v-model="direction" class="filter-select" @change="reload">
          <option value="">全部方向</option>
          <option value="received">收到</option>
          <option value="sent">发出</option>
        </select>
        <input v-model="q" class="search-input" placeholder="搜索号码/内容..." @keyup.enter="reload" />
        <button class="toolbar-btn" @click="reload">查询</button>
      </div>
      <div class="toolbar-right">
        <span v-if="syncing" class="muted">同步中...</span>
        <button class="toolbar-btn" :disabled="syncing" @click="syncAll">从板子同步</button>
      </div>
    </div>

    <div class="panel">
      <table v-if="items.length">
        <thead>
          <tr>
            <th style="width:150px">时间</th>
            <th style="width:150px">设备</th>
            <th style="width:60px">卡槽</th>
            <th style="width:60px">方向</th>
            <th style="width:130px">号码</th>
            <th>内容</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in items" :key="row.id">
            <td class="muted">{{ row.sms_time }}</td>
            <td>{{ row.device_name }}</td>
            <td>{{ row.sim_slot }}</td>
            <td>
              <span :class="['tag', row.direction === 'received' ? 'in' : 'out']">
                {{ row.direction === 'received' ? '收到' : '发出' }}
              </span>
            </td>
            <td class="mono">{{ row.phone }}</td>
            <td class="content">{{ row.content }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">{{ loading ? '加载中...' : '暂无短信记录' }}</div>
    </div>

    <Pagination
      :page="page"
      :pages="pages"
      :page-size="pageSize"
      :total="total"
      @change="p => { page = p; load() }"
    />
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore, useDevicesStore } from '../stores'
import AppHeader from '../components/AppHeader.vue'
import Pagination from '../components/Pagination.vue'
import { fetchSmsHistory, refreshMessages } from '../api/endpoints'
import { useTheme } from '../composables/useTheme'

const router = useRouter()
const authStore = useAuthStore()
const devicesStore = useDevicesStore()
const { isDark, toggleTheme } = useTheme()

const loading = ref(false)
const syncing = ref(false)
const q = ref('')
const direction = ref('')
const deviceId = ref(0)
const page = ref(1)
const pageSize = ref(30)
const total = ref(0)
const pages = ref(0)
const items = ref([])
const devices = ref([])

let timer = null

async function load() {
  loading.value = true
  try {
    const data = await fetchSmsHistory({
      page: page.value,
      pageSize: pageSize.value,
      q: q.value.trim(),
      direction: direction.value,
      deviceId: deviceId.value
    })
    items.value = data.items || []
    total.value = data.total || 0
    pages.value = data.pages || 0
  } finally {
    loading.value = false
  }
}

function reload() {
  page.value = 1
  load()
}

async function syncAll() {
  syncing.value = true
  try {
    for (const d of devices.value) {
      try { await refreshMessages(d.id) } catch { /* one dead board must not stop the rest */ }
    }
    await load()
  } finally {
    syncing.value = false
  }
}

async function handleLogout() {
  await authStore.logout(true)
  router.push('/login')
}

onMounted(async () => {
  if (!(await authStore.restore())) {
    router.push('/login')
    return
  }
  await devicesStore.refresh()
  devices.value = devicesStore.devices
  await load()
  timer = setInterval(load, 30000)
})

onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.messages-view { padding-bottom: 32px; }
.toolbar { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding: 12px 18px; }
.toolbar-left, .toolbar-right { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.filter-select, .search-input {
  background: var(--bg-input, #0f1629); color: var(--text, #e2e8f0);
  border: 1px solid var(--border, #253048); border-radius: 8px; padding: 8px 10px;
}
.search-input { min-width: 220px; }
.panel { margin: 0 18px; background: var(--bg-card, #111a2e); border: 1px solid var(--border, #253048); border-radius: 12px; overflow: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 10px 12px; border-bottom: 1px solid var(--border, #253048); text-align: left; vertical-align: top; }
th { color: var(--text-muted, #94a3b8); font-weight: 500; position: sticky; top: 0; background: var(--bg-card, #111a2e); }
.muted { color: var(--text-muted, #94a3b8); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.content { white-space: pre-wrap; word-break: break-word; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; }
.tag.in { background: rgba(16, 185, 129, 0.15); color: #34d399; }
.tag.out { background: rgba(59, 130, 246, 0.15); color: #60a5fa; }
.empty { padding: 40px; text-align: center; color: var(--text-muted, #94a3b8); }
</style>
