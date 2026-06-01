<script setup>
import { ref, computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'

import {
  useAuthStore,
  useDevicesStore,
  useNoticeStore,
  useScanStore
} from './stores'

import AppHeader from './components/AppHeader.vue'
import ConfigModal from './components/ConfigModal.vue'
import ConfirmModal from './components/ConfirmModal.vue'
import DetailModal from './components/DetailModal.vue'
import DeviceGrid from './components/DeviceGrid.vue'
import LoginView from './components/LoginView.vue'
import MessagePanel from './components/MessagePanel.vue'
import NoticeBar from './components/NoticeBar.vue'
import NumbersTable from './components/NumbersTable.vue'
import OtaModal from './components/OtaModal.vue'
import Pagination from './components/Pagination.vue'
import PromptModal from './components/PromptModal.vue'
import StatsGrid from './components/StatsGrid.vue'
import WifiModal from './components/WifiModal.vue'

import { useLoading } from './composables/useLoading'
import { useNotice } from './composables/useNotice'
import { useDeviceActions } from './composables/useDeviceActions'
import { useMessaging } from './composables/useMessaging'
import { useWifi } from './composables/useWifi'
import { useOta } from './composables/useOta'
import { useConfigBatch } from './composables/useConfigBatch'
import { useDetail } from './composables/useDetail'

// FIX(P2#5): App.vue is a thin shell. Auth, devices and scan state live in
// Pinia stores; per-workflow state and handlers (SMS/dial, WiFi, OTA, config
// batch, detail/SIM, device list actions) live in composables under
// src/composables/. App.vue only assembles the layout and owns the bits that
// are inseparable from the root template (login/logout, onMounted bootstrap,
// the active tab, and the page-aware select-all derivation).

const authStore = useAuthStore()
const devicesStore = useDevicesStore()
const scanStore = useScanStore()
const noticeStore = useNoticeStore()

const { authed, uiPass } = storeToRefs(authStore)
const { text: noticeText, type: noticeType } = storeToRefs(noticeStore)
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

const { refresh, startScanAdd, toggleSelectAll, batchDeleteSelected } = useDeviceActions()
const { commMode, fromSelected, toPhone, content, dialPhone, ttsText, send, dial } = useMessaging()
const { showWifiModal, wifiSsid, wifiPwd, wifiPreviewResults, openWifiModal, closeWifiModal, previewWifi, applyWifi } = useWifi()
const { showOtaModal, otaResults, otaUpgrading, openOtaModal, closeOtaModal, checkOta, upgradeOta } = useOta()
const { showConfigModal, openConfigModal } = useConfigBatch()
const { showDetailModal, deviceDetail, closeDetailModal, saveSimSingle, updateDetailSim } = useDetail()

// Composite notice payload preserved for child components that expect the
// legacy `{ text, type }` shape.
const notice = computed(() => ({ text: noticeText.value, type: noticeType.value }))

const activeTab = ref('devices')

// FIX(P2#7, Devin Review #8): the select-all checkbox's checked / indeterminate
// state must reflect *current page* membership to stay consistent with
// toggleSelectAll(), which only acts on the visible page.
const currentPageSelectedCount = computed(
  () => devices.value.filter(d => selectedIds.value.includes(d.id)).length
)

async function login() {
  loading.value = true
  try {
    const ok = await authStore.login()
    if (ok) await devicesStore.refresh()
  } finally {
    loading.value = false
  }
}

async function logout(showMsg = false) {
  // authStore.logout() also clears the device selection (see store impl for the
  // 401-interceptor regression note), so this wrapper is purely a thin adapter
  // for the LoginView's @logout event.
  await authStore.logout(showMsg)
}

onMounted(async () => {
  loading.value = true
  try {
    if (await authStore.restore()) {
      await devicesStore.refresh()
    }
  } finally {
    loading.value = false
  }
})
</script>


<template>
  <div class="app">
    <LoginView
      v-if="!authed"
      v-model:password="uiPass"
      :loading="loading"
      :notice="notice"
      @login="login"
    />

    <div v-else class="main-container">
      <AppHeader
        :loading="loading"
        :scanning="scanning"
        @scan="startScanAdd"
        @refresh="refresh"
        @logout="logout(true)"
      />
      <NoticeBar :notice="notice" @close="clearNotice" />

      <ConfigModal v-if="showConfigModal" />

      <StatsGrid
        :online="onlineCount"
        :offline="offlineCount"
        :total="devicesStore.devicesTotal"
        :sim-count="devicesStore.numbersTotal"
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
        <button :class="['tab-btn', { active: activeTab === 'numbers' }]" @click="activeTab = 'numbers'">
          号码列表 ({{ devicesStore.numbersTotal }})
        </button>
      </div>

      <DeviceGrid v-if="activeTab === 'devices'" />

      <Pagination
        v-if="activeTab === 'devices'"
        :page="devicesStore.devicesPage"
        :pages="devicesStore.devicesPages"
        :page-size="devicesStore.devicesPageSize"
        :total="devicesStore.devicesTotal"
        @change="devicesStore.setDevicesPage"
      />

      <NumbersTable v-if="activeTab === 'numbers'" />

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
    </div>

    <!-- FIX(P2#6): app-wide singletons that replace native window.prompt and
         window.confirm. Only ever one of each open at a time, so mounting them
         at the App root keeps the dialog store usable from anywhere. -->
    <ConfirmModal />
    <PromptModal />
  </div>
</template>
