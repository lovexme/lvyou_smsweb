<template>
  <div class="dashboard">
    <AppHeader
      :loading="loading"
      :scanning="scanning"
      :is-dark="isDark"
      @scan="startScanAdd"
      @refresh="refresh"
      @logout="handleLogout"
      @toggle-theme="toggleTheme"
      @open-settings="$router.push('/settings')"
    />

    <StatsGrid
      :online="onlineCount"
      :offline="offlineCount"
      :total="devicesStore.devicesTotal"
      :sim-count="devicesStore.numbersTotal"
      :trend-data="trendData"
    />

    <MessagePanel
      v-model:mode="commMode"
      v-model:sender="fromSelected"
      v-model:to-phone="toPhone"
      v-model:content="content"
      v-model:dial-phone="dialPhone"
      v-model:tts-text="ttsText"
      :numbers="allNumbers"
      :loading="loading"
      @send="send"
      @dial="dial"
    />

    <div class="toolbar">
      <div class="toolbar-left">
        <input v-model="searchText" class="search-input" placeholder="搜索设备/IP/MAC/号码..." />
        <select v-model="groupFilter" class="filter-select">
          <option value="all">全部分组</option>
          <option v-for="g in uniqueGroups.filter(x => x !== 'all')" :key="g" :value="g">{{ g }}</option>
        </select>
      </div>
      <div class="toolbar-right">
        <button class="toolbar-btn" @click="openWifiModal" :disabled="selectedCount === 0">WiFi</button>
        <button class="toolbar-btn" @click="openOtaModal" :disabled="selectedCount === 0">OTA</button>
        <button class="toolbar-btn" @click="openConfigModal" :disabled="selectedCount === 0">配置</button>
        <button class="toolbar-btn danger" @click="batchDeleteSelected" :disabled="selectedCount === 0">删除</button>
      </div>
    </div>

    <div class="select-bar">
      <label class="select-all-label">
        <span :class="['checkbox', { checked: currentPageSelectedCount > 0 && currentPageSelectedCount === filteredDevices.length }]">
          {{ currentPageSelectedCount > 0 && currentPageSelectedCount === filteredDevices.length ? '✓' : (currentPageSelectedCount > 0 ? '−' : '') }}
        </span>
        <input
          type="checkbox"
          :checked="currentPageSelectedCount === filteredDevices.length && filteredDevices.length > 0"
          :indeterminate="currentPageSelectedCount > 0 && currentPageSelectedCount < filteredDevices.length"
          @change="toggleSelectAll"
          style="display: none"
        />
        <span class="select-text">
          {{ selectedCount > 0 ? `已选择 ${selectedCount} 台` : '全选' }}
        </span>
      </label>
      <button v-if="selectedCount > 0" class="batch-cancel" @click="selectedIds = []">取消选择</button>
    </div>

    <div class="tab-bar">
      <button :class="['tab-btn', { active: activeTab === 'devices' }]" @click="activeTab = 'devices'">
        设备列表 ({{ devicesStore.devicesTotal }})
      </button>
      <button :class="['tab-btn', { active: activeTab === 'numbers' }]" @click="$router.push('/numbers')">
        号码列表 ({{ devicesStore.numbersTotal }})
      </button>
    </div>

    <DeviceGrid
      v-if="activeTab === 'devices'"
      :devices="filteredDevices"
      :selected-ids="selectedIds"
      @toggle-select="toggleSelect"
      @show-detail="showDetail"
      @rename="renameDevice"
      @set-group="setGroup"
      @delete="deleteDevice"
    />

    <Pagination
      v-if="activeTab === 'devices'"
      :page="devicesStore.devicesPage"
      :pages="devicesStore.devicesPages"
      :page-size="devicesStore.devicesPageSize"
      :total="devicesStore.devicesTotal"
      @change="devicesStore.setDevicesPage"
    />

    <WifiModal
      v-if="showWifiModal"
      v-model:ssid="wifiSsid"
      v-model:password="wifiPwd"
      :results="wifiPreviewResults"
      :loading="loading"
      @preview="previewWifi"
      @apply="applyWifi"
      @close="closeWifiModal"
    />

    <OtaModal
      v-if="showOtaModal"
      :results="otaResults"
      :loading="loading"
      :upgrading="otaUpgrading"
      @check="checkOta"
      @upgrade="upgradeOta"
      @close="closeOtaModal"
    />

    <DetailModal
      v-if="showDetailModal && deviceDetail"
      :detail="deviceDetail"
      :loading="loading"
      @update-sim1="updateDetailSim('sim1number', $event)"
      @update-sim2="updateDetailSim('sim2number', $event)"
      @save="saveSimSingle"
      @close="closeDetailModal"
    />

    <ConfigModal v-if="showConfigModal" />
    <ConfirmModal />
    <PromptModal />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import {
  useAuthStore,
  useDevicesStore,
  useNoticeStore,
  useScanStore
} from '../stores'
import AppHeader from '../components/AppHeader.vue'
import StatsGrid from '../components/StatsGrid.vue'
import MessagePanel from '../components/MessagePanel.vue'
import DeviceGrid from '../components/DeviceGrid.vue'
import Pagination from '../components/Pagination.vue'
import WifiModal from '../components/WifiModal.vue'
import OtaModal from '../components/OtaModal.vue'
import DetailModal from '../components/DetailModal.vue'
import ConfigModal from '../components/ConfigModal.vue'
import ConfirmModal from '../components/ConfirmModal.vue'
import PromptModal from '../components/PromptModal.vue'
import { useLoading } from '../composables/useLoading'
import { useNotice } from '../composables/useNotice'
import { useDeviceActions } from '../composables/useDeviceActions'
import { useMessaging } from '../composables/useMessaging'
import { useWifi } from '../composables/useWifi'
import { useOta } from '../composables/useOta'
import { useConfigBatch } from '../composables/useConfigBatch'
import { useDetail } from '../composables/useDetail'
import { useKeyboardShortcuts } from '../composables/useKeyboardShortcuts'
import { useTheme } from '../composables/useTheme'

const router = useRouter()
const authStore = useAuthStore()
const devicesStore = useDevicesStore()
const scanStore = useScanStore()
const noticeStore = useNoticeStore()

const { authed, uiPass } = storeToRefs(authStore)
const {
  devices,
  allNumbers,
  searchText,
  groupFilter,
  selectedIds,
  uniqueGroups,
  onlineCount,
  offlineCount,
  selectedCount,
  filteredDevices
} = storeToRefs(devicesStore)
const { scanning } = storeToRefs(scanStore)

const loading = useLoading()
const { clearNotice } = useNotice()
const { isDark, toggleTheme } = useTheme()

const { refresh, startScanAdd, toggleSelectAll, batchDeleteSelected, toggleSelect, renameDevice, setGroup, deleteDevice } = useDeviceActions()
const { commMode, fromSelected, toPhone, content, dialPhone, ttsText, send, dial } = useMessaging()
const { showWifiModal, wifiSsid, wifiPwd, wifiPreviewResults, openWifiModal, closeWifiModal, previewWifi, applyWifi } = useWifi()
const { showOtaModal, otaResults, otaUpgrading, openOtaModal, closeOtaModal, checkOta, upgradeOta } = useOta()
const { showConfigModal, openConfigModal } = useConfigBatch()
const { showDetailModal, deviceDetail, closeDetailModal, saveSimSingle, updateDetailSim } = useDetail()

const activeTab = ref('devices')
const trendData = ref({
  online: [65, 72, 68, 75, 80, 78, 82],
  offline: [15, 12, 14, 10, 8, 10, 6],
  total: [80, 84, 82, 85, 88, 88, 88],
  sim: [160, 168, 164, 170, 176, 176, 176]
})

const currentPageSelectedCount = computed(
  () => devices.value.filter(d => selectedIds.value.includes(d.id)).length
)

async function handleLogout() {
  await authStore.logout(true)
  router.push('/login')
}

// Keyboard shortcuts
const shortcuts = {
  'Ctrl+K': () => document.querySelector('.search-input')?.focus(),
  'Space': () => { if (document.activeElement.tagName !== 'INPUT') toggleSelectAll() },
  'Delete': () => { if (selectedCount.value > 0) batchDeleteSelected() },
  'r': () => { if (document.activeElement.tagName !== 'INPUT') refresh() }
}
useKeyboardShortcuts(shortcuts)

onMounted(async () => {
  loading.value = true
  try {
    if (await authStore.restore()) {
      await devicesStore.refresh()
    } else {
      router.push('/login')
    }
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.dashboard { padding: 24px; max-width: 1440px; margin: 0 auto; }
</style>
