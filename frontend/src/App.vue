<script setup>
import { ref, computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'

import {
  applyWifiBatch,
  checkOtaBatch,
  fetchDeviceDetail,
  previewConfigPreset,
  previewDeviceConfig,
  previewWifiBatch,
  readDeviceConfigs,
  saveDeviceSim,
  sendSms,
  dialDevice,
  upgradeOtaBatch,
  writeConfigPreset,
  writeDeviceConfig
} from './api/endpoints'

import {
  useAuthStore,
  useDevicesStore,
  useDialogStore,
  useNoticeStore,
  useScanStore
} from './stores'

import AppHeader from './components/AppHeader.vue'
import ConfirmModal from './components/ConfirmModal.vue'
import DetailModal from './components/DetailModal.vue'
import LoginView from './components/LoginView.vue'
import NoticeBar from './components/NoticeBar.vue'
import OtaModal from './components/OtaModal.vue'
import MessagePanel from './components/MessagePanel.vue'
import Pagination from './components/Pagination.vue'
import PromptModal from './components/PromptModal.vue'
import StatsGrid from './components/StatsGrid.vue'
import WifiModal from './components/WifiModal.vue'
import { displayName, prettyTime } from './utils/format'

// FIX(P2#5): App.vue is now a thin shell. Auth, devices and scan state
// (the four most reused groups) live in Pinia stores; the component
// keeps only the bits that are inseparable from the template -- modal
// open/close flags, the SMS/dial/OTA/WiFi/config workflows, and the
// thin wrappers below that bridge template events to store actions.

const authStore = useAuthStore()
const noticeStore = useNoticeStore()
const devicesStore = useDevicesStore()
const scanStore = useScanStore()
const dialogStore = useDialogStore()

const { authed, uiPass } = storeToRefs(authStore)
const { text: noticeText, type: noticeType } = storeToRefs(noticeStore)
const {
  devices,
  numbers,
  allNumbers,
  searchText,
  groupFilter,
  selectedIds,
  uniqueGroups,
  onlineCount,
  offlineCount,
  selectedCount,
  filteredDevices,
  filteredNumbers
} = storeToRefs(devicesStore)
const { scanning } = storeToRefs(scanStore)

// Composite notice payload preserved for child components that expect
// the legacy `{ text, type }` shape.
const notice = computed(() => ({ text: noticeText.value, type: noticeType.value }))

// FIX(P2#7, Devin Review #8): the select-all checkbox's checked /
// indeterminate state must reflect *current page* membership to stay
// consistent with toggleSelectAll(), which only acts on the visible
// page. Without this, selecting page 1 (100/100 items) and paginating
// to page 2 would leave the header checkbox visually fully-checked
// even though no item on page 2 is selected.
const currentPageSelectedCount = computed(
  () => devices.value.filter(d => selectedIds.value.includes(d.id)).length
)

// `loading` is the shared "something is in flight" spinner used by every
// modal and toolbar button. It still lives in App.vue because a handful
// of unmoved workflows (SMS, dial, OTA, WiFi, config IO, detail/SIM)
// drive it directly. Store actions toggle their own loading flags;
// the thin wrappers below mirror them into this ref for the global UI.
const loading = ref(false)

const activeTab = ref('devices')

const fromSelected = ref('')
const toPhone = ref('')
const content = ref('')

const commMode = ref('sms')
const dialPhone = ref('')
const ttsText = ref('')

const showWifiModal = ref(false)
const showDetailModal = ref(false)
const showOtaModal = ref(false)
const showConfigModal = ref(false)

const wifiSsid = ref('')
const wifiPwd = ref('')
const deviceDetail = ref(null)
const wifiPreviewResults = ref([])

const otaResults = ref([])
const otaUpgrading = ref(false)

const configStep = ref('read')
const configData = ref([])
const configPattern = ref('')
const configReplacement = ref('')
const configFlags = ref('s')
const configPreviewData = ref([])
const configExpandedIds = ref([])
const configMode = ref('regex')

function setNotice(text, type = 'info') {
  noticeStore.set(text, type)
}

function clearNotice() {
  noticeStore.clear()
}

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
  // authStore.logout() also clears the device selection (see store impl
  // for the 401-interceptor regression note), so this wrapper is purely
  // a thin adapter for the LoginView's @logout event.
  await authStore.logout(showMsg)
}

async function refresh() {
  loading.value = true
  try {
    await devicesStore.refresh()
  } finally {
    loading.value = false
  }
}

async function startScanAdd() {
  await scanStore.start()
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

function toggleSelectAll() {
  devicesStore.toggleSelectAll()
}

function toggleSelect(id) {
  devicesStore.toggleSelect(id)
}

function isSelected(id) {
  return devicesStore.isSelected(id)
}

async function renameDevice(device) {
  const name = await dialogStore.prompt({
    title: '修改设备别名',
    label: '请输入设备别名：',
    defaultValue: device.alias || '',
    placeholder: '别名'
  })
  if (name === null) return
  await devicesStore.rename(device, name)
}

async function setGroup(device) {
  const group = await dialogStore.prompt({
    title: '修改设备分组',
    label: '请输入分组名称：',
    defaultValue: device.grp || 'auto',
    placeholder: '分组'
  })
  if (group === null) return
  await devicesStore.regroup(device, group)
}

async function deleteDevice(device) {
  const ok = await dialogStore.confirm({
    title: '删除设备',
    message: `确认删除设备 ${displayName(device)}？`,
    confirmText: '删除',
    danger: true
  })
  if (!ok) return
  loading.value = true
  try {
    await devicesStore.deleteOne(device)
  } finally {
    loading.value = false
  }
}

async function batchDeleteSelected() {
  if (!selectedCount.value) {
    setNotice('请先勾选设备', 'err')
    return
  }
  const ok = await dialogStore.confirm({
    title: '批量删除',
    message: `确认删除所选 ${selectedCount.value} 台设备？`,
    confirmText: '删除',
    danger: true
  })
  if (!ok) return
  loading.value = true
  try {
    await devicesStore.batchDeleteSelected()
  } finally {
    loading.value = false
  }
}

function parseSenderValue() {
  const raw = String(fromSelected.value || '')
  const parts = raw.split('|')
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null
  const deviceId = Number(parts[0])
  const slot = Number(parts[1])
  if (!Number.isInteger(deviceId) || !Number.isInteger(slot)) return null
  return { deviceId, slot }
}

async function send() {
  if (!fromSelected.value || !toPhone.value || !content.value) {
    setNotice('请填写完整', 'err')
    return
  }
  const sender = parseSenderValue()
  if (!sender) {
    setNotice('请选择有效的发送卡号', 'err')
    return
  }
  loading.value = true
  try {
    await sendSms({
      deviceId: sender.deviceId,
      phone: toPhone.value,
      content: content.value,
      slot: sender.slot
    })
    setNotice('已发送', 'ok')
    toPhone.value = ''
    content.value = ''
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || e.message || '发送失败', 'err')
  } finally {
    loading.value = false
  }
}

async function dial() {
  if (!fromSelected.value || !dialPhone.value) {
    setNotice('请填写完整', 'err')
    return
  }
  const sender = parseSenderValue()
  if (!sender) {
    setNotice('请选择有效的拨号卡号', 'err')
    return
  }
  loading.value = true
  try {
    await dialDevice({
      deviceId: sender.deviceId,
      phone: dialPhone.value,
      slot: sender.slot,
      tts: ttsText.value
    })
    setNotice('已拨出', 'ok')
    dialPhone.value = ''
    ttsText.value = ''
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || e.message || '拨号失败', 'err')
  } finally {
    loading.value = false
  }
}

function openWifiModal() {
  if (!selectedCount.value) {
    setNotice('请先勾选设备', 'err')
    return
  }
  showWifiModal.value = true
}

function closeWifiModal() {
  showWifiModal.value = false
  wifiSsid.value = ''
  wifiPwd.value = ''
  wifiPreviewResults.value = []
}

function openOtaModal() {
  if (!selectedCount.value) {
    setNotice('请先勾选设备', 'err')
    return
  }
  otaResults.value = []
  otaUpgrading.value = false
  showOtaModal.value = true
}

function closeOtaModal() {
  showOtaModal.value = false
  otaResults.value = []
}

async function checkOta() {
  loading.value = true
  try {
    const response = await checkOtaBatch(selectedIds.value)
    otaResults.value = response.data && response.data.results ? response.data.results : []
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || e.message || '检查失败', 'err')
  } finally {
    loading.value = false
  }
}

async function upgradeOta() {
  const hasUpdateDevices = otaResults.value.filter(r => r.ok && r.hasUpdate)
  if (hasUpdateDevices.length === 0) {
    setNotice('没有可升级的设备', 'warn')
    return
  }
  const ok = await dialogStore.confirm({
    title: '确认 OTA 升级',
    message: `确定要升级 ${hasUpdateDevices.length} 台设备吗？设备会重启。`,
    confirmText: '升级',
    danger: true
  })
  if (!ok) return
  otaUpgrading.value = true
  loading.value = true
  try {
    const response = await upgradeOtaBatch(selectedIds.value)
    const results = response.data && response.data.results ? response.data.results : []
    const okCount = results.filter(r => r.ok).length
    setNotice('OTA升级完成：' + okCount + '/' + results.length, okCount ? 'ok' : 'err')
    closeOtaModal()
    await devicesStore.refresh()
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || e.message || '升级失败', 'err')
  } finally {
    otaUpgrading.value = false
    loading.value = false
  }
}

function openConfigModal() {
  if (!selectedCount.value) {
    setNotice('请先勾选设备', 'err')
    return
  }
  configStep.value = 'read'
  configData.value = []
  configPattern.value = ''
  configReplacement.value = ''
  configFlags.value = 's'
  configPreviewData.value = []
  configExpandedIds.value = []
  configMode.value = 'regex'
  showConfigModal.value = true
}

function closeConfigModal() {
  showConfigModal.value = false
  configData.value = []
  configPreviewData.value = []
  configExpandedIds.value = []
}

async function readConfigs() {
  if (!selectedCount.value) {
    setNotice('请先勾选设备', 'err')
    return
  }
  loading.value = true
  try {
    const resp = await readDeviceConfigs(selectedIds.value)
    configData.value = resp.data && resp.data.configs ? resp.data.configs : []
    configStep.value = 'edit'
    const firstOk = configData.value.find(item => item.ok)
    configExpandedIds.value = firstOk ? [firstOk.id] : []
    setNotice(`读取完成：${configData.value.filter(item => item.ok).length}/${configData.value.length}`, 'info')
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || '读取配置失败', 'err')
  } finally {
    loading.value = false
  }
}

function toggleConfigExpand(id) {
  const idx = configExpandedIds.value.indexOf(id)
  if (idx > -1) {
    configExpandedIds.value.splice(idx, 1)
  } else {
    configExpandedIds.value.push(id)
  }
}

async function previewConfig() {
  if (!configPattern.value.trim()) {
    setNotice('请输入正则表达式', 'err')
    return
  }
  loading.value = true
  try {
    const resp = await previewDeviceConfig({
      device_ids: selectedIds.value,
      pattern: configPattern.value,
      replacement: configReplacement.value,
      flags: configFlags.value
    })
    configPreviewData.value = resp.data && resp.data.previews ? resp.data.previews : []
    configStep.value = 'preview'
    const firstChanged = configPreviewData.value.find(item => item.ok && item.changed)
    const firstResult = firstChanged || configPreviewData.value[0]
    configExpandedIds.value = firstResult ? [firstResult.id] : []
    setNotice(`预览完成：${configPreviewData.value.filter(item => item.ok && item.changed).length} 台有变更`, 'info')
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || '预览失败', 'err')
  } finally {
    loading.value = false
  }
}

async function previewCleanMessageTemplates() {
  loading.value = true
  try {
    const resp = await previewConfigPreset(selectedIds.value, 'clean_message_templates')
    configPreviewData.value = resp.data && resp.data.previews ? resp.data.previews : []
    configStep.value = 'preview'
    configMode.value = 'clean_message_templates'
    const firstChanged = configPreviewData.value.find(item => item.ok && item.changed)
    const firstResult = firstChanged || configPreviewData.value[0]
    configExpandedIds.value = firstResult ? [firstResult.id] : []
    setNotice(`简洁模板预览完成：${configPreviewData.value.filter(item => item.ok && item.changed).length} 台有变更`, 'info')
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || '预览失败', 'err')
  } finally {
    loading.value = false
  }
}

async function writeConfigs() {
  const changedCount = configPreviewData.value.filter(item => item.ok && item.changed).length
  if (!changedCount) {
    setNotice('预览没有发现可写入的变更', 'warn')
    return
  }
  const modeText = configMode.value === 'clean_message_templates' ? '应用简洁消息模板' : '按正则替换'
  const ok = await dialogStore.confirm({
    title: '确认写入配置',
    message: `确认对 ${changedCount} 台设备写入配置？\n本次操作：${modeText}。\n会先重新读取每台设备当前配置，写入后再读回校验。`,
    confirmText: '写入',
    danger: true
  })
  if (!ok) return
  loading.value = true
  try {
    const payload = { device_ids: selectedIds.value }
    const resp = configMode.value === 'clean_message_templates'
      ? await writeConfigPreset(selectedIds.value, 'clean_message_templates')
      : await writeDeviceConfig({
        ...payload,
        pattern: configPattern.value,
        replacement: configReplacement.value,
        flags: configFlags.value
      })
    const results = resp.data && resp.data.results ? resp.data.results : []
    const okCount = results.filter(item => item.ok).length
    const changed = results.filter(item => item.changed).length
    setNotice(`配置写入完成：${okCount}/${results.length} 成功，${changed} 台有变更`, okCount ? 'ok' : 'err')
    closeConfigModal()
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || '写入失败', 'err')
  } finally {
    loading.value = false
  }
}

function diffLines(original, replaced) {
  const oLines = (original || '').split('\n')
  const rLines = (replaced || '').split('\n')
  const maxLen = Math.max(oLines.length, rLines.length)
  const lines = []
  for (let i = 0; i < maxLen; i++) {
    const o = oLines[i] !== undefined ? oLines[i] : ''
    const r = rLines[i] !== undefined ? rLines[i] : ''
    if (o === r) {
      lines.push({ type: 'same', text: o })
    } else {
      if (o) lines.push({ type: 'del', text: o })
      if (r) lines.push({ type: 'add', text: r })
    }
  }
  return lines
}

async function previewWifi() {
  if (!wifiSsid.value.trim()) {
    setNotice('请输入SSID', 'err')
    return
  }
  if (!selectedCount.value) {
    setNotice('请先勾选设备', 'err')
    return
  }
  loading.value = true
  try {
    const response = await previewWifiBatch({
      device_ids: selectedIds.value,
      ssid: wifiSsid.value.trim(),
      pwd: wifiPwd.value.trim()
    })
    wifiPreviewResults.value = response.data && response.data.results ? response.data.results : []
    setNotice(`预览完成：共 ${wifiPreviewResults.value.length} 台设备`, 'ok')
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || e.message || '预览失败', 'err')
    wifiPreviewResults.value = []
  } finally {
    loading.value = false
  }
}

async function applyWifi() {
  if (!wifiSsid.value.trim()) {
    setNotice('请输入SSID', 'err')
    return
  }
  if (!selectedCount.value) {
    setNotice('请先勾选设备', 'err')
    return
  }
  loading.value = true
  try {
    const response = await applyWifiBatch({
      device_ids: selectedIds.value,
      ssid: wifiSsid.value.trim(),
      pwd: wifiPwd.value.trim()
    })
    const list = response.data && response.data.results ? response.data.results : []
    const okCount = list.filter(item => item.ok).length
    setNotice('WiFi 添加完成：' + okCount + '/' + list.length, okCount ? 'ok' : 'err')
    wifiPreviewResults.value = []
    closeWifiModal()
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || e.message || '配置失败', 'err')
  } finally {
    loading.value = false
  }
}

async function showDetail(device) {
  loading.value = true
  try {
    const response = await fetchDeviceDetail(device.id)
    deviceDetail.value = response.data
    showDetailModal.value = true
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || e.message || '获取详情失败', 'err')
  } finally {
    loading.value = false
  }
}

function closeDetailModal() {
  showDetailModal.value = false
  deviceDetail.value = null
}

async function saveSimSingle() {
  const id = deviceDetail.value && deviceDetail.value.device && deviceDetail.value.device.id
  if (!id) return
  loading.value = true
  try {
    await saveDeviceSim(id, {
      sim1: deviceDetail.value.device.sim1number || '',
      sim2: deviceDetail.value.device.sim2number || ''
    })
    setNotice('已保存卡号', 'ok')
    await devicesStore.refresh()
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || e.message || '保存失败', 'err')
  } finally {
    loading.value = false
  }
}

function updateDetailSim(field, value) {
  if (deviceDetail.value && deviceDetail.value.device) {
    deviceDetail.value.device[field] = value
  }
}
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

      <div v-if="showConfigModal" class="modal-overlay" @click.self="closeConfigModal">
        <div class="modal modal-xl">
          <div class="modal-header">
            <h3>批量设备配置</h3>
            <button class="modal-close" @click="closeConfigModal">×</button>
          </div>
          <div class="modal-body">
            <div v-if="configStep === 'read'" class="config-intro">
              <p class="config-info">先读取 {{ selectedCount }} 台设备当前配置，再用正则只替换匹配到的片段，避免覆盖每台设备不同的数据。</p>
              <button class="btn-confirm full-width" @click="readConfigs" :disabled="loading">
                {{ loading ? '读取中...' : '读取配置' }}
              </button>
            </div>

            <div v-if="configStep === 'edit'" class="config-flow">
              <div class="config-devices-list">
                <div v-for="c in configData" :key="c.id" class="config-device-item">
                  <div class="config-device-header" @click="toggleConfigExpand(c.id)">
                    <span class="mono">{{ c.ip }}</span>
                    <span :class="['config-status', c.ok ? 'ok' : 'err']">{{ c.ok ? '已读取' : '失败' }}</span>
                    <span class="config-expand-icon">{{ configExpandedIds.includes(c.id) ? '▼' : '▶' }}</span>
                  </div>
                  <div v-if="configExpandedIds.includes(c.id)" class="config-content">
                    <pre v-if="c.ok" class="config-pre">{{ c.config }}</pre>
                    <span v-else class="config-error">{{ c.error }}</span>
                  </div>
                </div>
              </div>
              <div class="config-regex-section">
                <p class="config-section-title">小白模式</p>
                <p class="config-hint">自动保留前面的主 JSON 配置，只替换后面的消息模板区，避免把设备配置改坏。</p>
                <button class="btn-confirm full-width" @click="previewCleanMessageTemplates" :disabled="loading">
                  应用简洁消息模板（推荐）
                </button>
              </div>
              <div class="config-regex-section">
                <p class="config-section-title">正则替换规则</p>
                <textarea v-model="configPattern" class="form-textarea-full" rows="4" placeholder="正则表达式，例如：(?s)&quot;uip&quot;:\\s*\\[.*?\\]\\s*(?=,\\s*&quot;sysArgs&quot;)"></textarea>
                <textarea v-model="configReplacement" class="form-textarea-full" rows="5" placeholder="替换文本：只填写要替换进去的片段"></textarea>
                <input v-model="configFlags" class="form-input" placeholder="标志位：i 忽略大小写，m 多行，s 点号匹配换行" />
                <p class="config-hint">不要把开头主 JSON 替换成 {}。建议只匹配消息模板区或 uip 固定片段，先预览确认主 JSON 还完整。</p>
                <div class="config-btn-row">
                  <button class="btn-cancel" @click="configStep = 'read'">上一步</button>
                  <button class="btn-confirm" @click="previewConfig" :disabled="loading || !configPattern.trim()">预览替换</button>
                </div>
              </div>
            </div>

            <div v-if="configStep === 'preview'" class="config-flow">
              <div class="config-devices-list">
                <div v-for="p in configPreviewData" :key="p.id" class="config-device-item">
                  <div class="config-device-header" @click="toggleConfigExpand(p.id)">
                    <span class="mono">{{ p.ip }}</span>
                    <span v-if="configMode === 'clean_message_templates'" class="config-status info">简洁模板</span>
                    <span v-if="p.ok" :class="['config-status', p.changed ? 'warn' : 'ok']">{{ p.changed ? '有变更' : '无变更' }}</span>
                    <span v-else class="config-status err">错误</span>
                    <span class="config-expand-icon">{{ configExpandedIds.includes(p.id) ? '▼' : '▶' }}</span>
                  </div>
                  <div v-if="configExpandedIds.includes(p.id)" class="config-content">
                    <div v-if="p.ok && p.changed" class="config-diff">
                      <div v-for="(line, idx) in diffLines(p.original, p.replaced)" :key="idx" :class="['diff-line', 'diff-' + line.type]">
                        <span class="diff-prefix">{{ line.type === 'add' ? '+' : line.type === 'del' ? '-' : ' ' }}</span>{{ line.text }}
                      </div>
                    </div>
                    <pre v-else-if="p.ok" class="config-pre">{{ p.original }}</pre>
                    <span v-else class="config-error">{{ p.error }}</span>
                  </div>
                </div>
              </div>
              <div class="config-btn-row">
                <button class="btn-cancel" @click="configStep = 'edit'">返回修改</button>
                <button class="btn-confirm danger-btn" @click="writeConfigs" :disabled="loading || !configPreviewData.filter(item => item.ok && item.changed).length">确认写入</button>
              </div>
            </div>
          </div>
        </div>
      </div>

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

      <div v-if="activeTab === 'devices'" class="cards-grid">
        <div v-if="filteredDevices.length === 0" class="empty-state">
          <div class="empty-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 6h18V4H4c-1.1 0-2 .9-2 2v11H0v3h14v-3H4V6z"/></svg>
          </div>
          <p>暂无设备，请先扫描</p>
          <button class="empty-btn" @click="startScanAdd" :disabled="scanning">
            {{ scanning ? '扫描中...' : '开始扫描' }}
          </button>
        </div>

        <div
          v-for="d in filteredDevices"
          :key="d.id"
          class="device-card"
          :class="{ selected: isSelected(d.id), offline: d.status !== 'online' }"
        >
          <div class="card-header">
            <div class="card-checkbox" @click="toggleSelect(d.id)">
              <span :class="['checkbox', { checked: isSelected(d.id) }]">✓</span>
            </div>
            <div class="card-status" :class="d.status">
              {{ d.status === 'online' ? '在线' : '离线' }}
            </div>
          </div>

          <div class="card-body">
            <div class="device-name">{{ displayName(d) }}</div>
            <div class="device-ip">{{ d.ip }}</div>
            <div class="device-mac">{{ d.mac || '-' }}</div>

            <div
              v-if="(d.sims && d.sims.sim1 && (d.sims.sim1.number || d.sims.sim1.operator)) || (d.sims && d.sims.sim2 && (d.sims.sim2.number || d.sims.sim2.operator))"
              class="sims-info"
            >
              <div v-if="d.sims && d.sims.sim1 && (d.sims.sim1.number || d.sims.sim1.operator)" class="sim-item">
                <span class="sim-label">SIM1</span>
                <span class="sim-op">{{ d.sims.sim1.operator || '-' }}</span>
                <span class="sim-signal" v-if="d.sims.sim1.signal > 0">{{ d.sims.sim1.signal }}%</span>
                <span class="sim-num">{{ d.sims.sim1.number || '-' }}</span>
              </div>
              <div v-if="d.sims && d.sims.sim2 && (d.sims.sim2.number || d.sims.sim2.operator)" class="sim-item">
                <span class="sim-label">SIM2</span>
                <span class="sim-op">{{ d.sims.sim2.operator || '-' }}</span>
                <span class="sim-signal" v-if="d.sims.sim2.signal > 0">{{ d.sims.sim2.signal }}%</span>
                <span class="sim-num">{{ d.sims.sim2.number || '-' }}</span>
              </div>
            </div>

            <div class="device-meta">
              <span class="device-group">{{ d.grp || 'auto' }}</span>
              <span class="device-time">{{ prettyTime(d.lastSeen) }}</span>
            </div>
          </div>

          <div class="card-actions">
            <button class="card-btn" @click="showDetail(d)" title="详情" aria-label="详情">
              <svg viewBox="0 0 20 20" fill="currentColor"><path d="M10 12a2 2 0 100-4 2 2 0 000 4z"/><path fill-rule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" clip-rule="evenodd"/></svg>
            </button>
            <button class="card-btn" @click="renameDevice(d)" title="改名" aria-label="改名">
              <svg viewBox="0 0 20 20" fill="currentColor"><path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"/></svg>
            </button>
            <button class="card-btn" @click="setGroup(d)" title="分组" aria-label="分组">
              <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M17.707 9.293a1 1 0 010 1.414l-7 7a1 1 0 01-1.414 0l-7-7A.997.997 0 012 10V5a3 3 0 013-3h5c.256 0 .512.098.707.293l7 7zM5 6a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd"/></svg>
            </button>
            <button class="card-btn danger" @click="deleteDevice(d)" title="删除" aria-label="删除">
              <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>
            </button>
          </div>
        </div>
      </div>

      <Pagination
        v-if="activeTab === 'devices'"
        :page="devicesStore.devicesPage"
        :pages="devicesStore.devicesPages"
        :page-size="devicesStore.devicesPageSize"
        :total="devicesStore.devicesTotal"
        @change="devicesStore.setDevicesPage"
      />

      <div v-if="activeTab === 'numbers'" class="numbers-table">
        <div v-if="filteredNumbers.length === 0" class="empty-state">
          <div class="empty-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 4H4c-1.11 0-1.99.89-1.99 2L2 18c0 1.11.89 2 2 2h16c1.11 0 2-.89 2-2V6c0-1.11-.89-2-2-2zm0 14H4v-6h16v6zm0-10H4V6h16v2z"/></svg>
          </div>
          <p>暂无号码数据</p>
        </div>
        <table v-else>
          <thead>
            <tr>
              <th>号码</th><th>运营商</th><th>设备</th><th>IP</th><th>槽位</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="n in filteredNumbers" :key="String(n.deviceId) + '-' + String(n.slot)">
              <td class="mono">{{ n.number }}</td>
              <td>{{ n.operator || '-' }}</td>
              <td>{{ n.deviceName }}</td>
              <td class="mono">{{ n.ip }}</td>
              <td>SIM{{ n.slot }}</td>
            </tr>
          </tbody>
        </table>
        <Pagination
          :page="devicesStore.numbersPage"
          :pages="devicesStore.numbersPages"
          :page-size="devicesStore.numbersPageSize"
          :total="devicesStore.numbersTotal"
          @change="devicesStore.setNumbersPage"
        />
      </div>

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

    <!-- FIX(P2#6): app-wide singletons that replace native window.prompt
         and window.confirm. Only ever one of each open at a time, so
         mounting them at the App root keeps the dialog store usable
         from anywhere (LoginView too, when authed === false). -->
    <ConfirmModal />
    <PromptModal />
  </div>
</template>

<style>
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

:root {
  --primary: #0a84ff;
  --primary-dark: #0066cc;
  --success: #30d158;
  --danger: #ff453a;
  --warning: #ff9f0a;
  --bg-dark: #000000;
  --bg-card: #1c1c1e;
  --bg-card-hover: #2c2c2e;
  --text-primary: #ffffff;
  --text-secondary: #8e8e93;
  --border: #38383a;
}

/* icon sizing for SVGs in header/stat/card slots */
.login-icon svg { width: 48px; height: 48px; color: var(--primary); }
.logo svg { width: 28px; height: 28px; color: var(--primary); }
.stat-icon { display: flex; align-items: center; justify-content: center; width: 44px; height: 44px; border-radius: 10px; }
.stat-icon svg { width: 22px; height: 22px; }
.stat-card.total .stat-icon { background: rgba(10,132,255,0.15); color: var(--primary); }
.stat-card.sim .stat-icon { background: rgba(255,159,10,0.15); color: var(--warning); }
.stat-card.online .stat-icon { background: rgba(48,209,88,0.15); }
.stat-card.offline .stat-icon { background: rgba(255,69,58,0.15); }
.stat-dot { display: block; width: 10px; height: 10px; border-radius: 50%; }
.stat-dot-online { background: var(--success); box-shadow: 0 0 0 4px rgba(48,209,88,0.18); }
.stat-dot-offline { background: var(--danger); box-shadow: 0 0 0 4px rgba(255,69,58,0.18); }
.empty-icon svg { width: 48px; height: 48px; color: var(--text-secondary); }
.card-btn svg { width: 16px; height: 16px; }

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg-dark);
  color: var(--text-primary);
  min-height: 100vh;
}

.login-container { display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 20px; }
.login-box { background: var(--bg-card); border-radius: 16px; padding: 40px; width: 100%; max-width: 380px; text-align: center; }
.login-icon { display: flex; align-items: center; justify-content: center; width: 64px; height: 64px; margin: 0 auto 16px; background: rgba(10,132,255,0.12); border-radius: 16px; color: var(--primary); }
.login-title { font-size: 22px; font-weight: 600; margin-bottom: 8px; }
.login-subtitle { color: var(--text-secondary); font-size: 13px; margin-bottom: 24px; }
.login-form { display: flex; flex-direction: column; gap: 12px; }
.login-input { background: var(--bg-dark); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; font-size: 16px; color: var(--text-primary); outline: none; }
.login-input:focus { border-color: var(--primary); }
.login-button { background: var(--primary); color: white; border: none; border-radius: 8px; padding: 14px; font-size: 16px; font-weight: 500; cursor: pointer; }
.login-button:hover:not(:disabled) { background: var(--primary-dark); }
.login-button:disabled { opacity: 0.6; cursor: not-allowed; }
.login-notice { margin-top: 14px; padding: 10px 14px; border-radius: 8px; font-size: 14px; }

.notice-ok { background: rgba(48,209,88,0.15); color: var(--success); }
.notice-err { background: rgba(255,69,58,0.15); color: var(--danger); }
.notice-info { background: rgba(10,132,255,0.15); color: var(--primary); }
.notice-warn { background: rgba(255,159,10,0.15); color: var(--warning); }

.main-container { padding: 20px; max-width: 1400px; margin: 0 auto; }

.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 16px; }
.header-left { display: flex; align-items: center; gap: 12px; }
.logo { width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; }
.header-title h1 { font-size: 20px; font-weight: 600; }
.header-title p { font-size: 12px; color: var(--text-secondary); }
.header-right { display: flex; gap: 8px; flex-wrap: wrap; }
.header-btn { background: var(--bg-card); border: 1px solid var(--border); color: var(--text-primary); padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 14px; }
.header-btn:hover:not(:disabled) { background: var(--bg-card-hover); }
.header-btn.primary { background: var(--primary); border-color: var(--primary); color: white; }
.header-btn.primary:hover:not(:disabled) { background: var(--primary-dark); }
.header-btn.logout { color: var(--danger); }
.header-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.notice-bar { padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }
.notice-close { background: none; border: none; color: inherit; font-size: 20px; cursor: pointer; }

.stats-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 16px; margin-bottom: 20px; }
@media (max-width: 768px) { .stats-grid { grid-template-columns: repeat(2,1fr); } }
.stat-card { background: var(--bg-card); border-radius: 12px; padding: 20px; display: flex; align-items: center; gap: 16px; }

.stat-value { font-size: 28px; font-weight: 700; }
.stat-label { font-size: 13px; color: var(--text-secondary); }
.stat-card.online .stat-value { color: var(--success); }
.stat-card.offline .stat-value { color: var(--danger); }
.stat-card.total .stat-value { color: var(--primary); }
.stat-card.sim .stat-value { color: var(--warning); }

.sms-section { background: var(--bg-card); border-radius: 12px; padding: 20px; margin-bottom: 20px; }
.section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.section-header h2 { font-size: 16px; font-weight: 600; }
.mode-tabs { display: flex; gap: 4px; }
.mode-tab { background: var(--bg-dark); border: none; color: var(--text-secondary); padding: 7px 16px; border-radius: 6px; cursor: pointer; }
.mode-tab.active { background: var(--primary); color: white; }

.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.form-grid .form-select,
.form-grid .form-input { background: var(--bg-dark); border: 1px solid var(--border); border-radius: 8px; padding: 12px; color: var(--text-primary); font-size: 14px; outline: none; }
.form-grid .form-select option { background: #1c1c1e; color: #ffffff; }
.form-grid .form-textarea { grid-column: span 2; background: var(--bg-dark); border: 1px solid var(--border); border-radius: 8px; padding: 12px; color: var(--text-primary); font-size: 14px; resize: vertical; outline: none; }
.form-grid .btn-send { grid-column: span 2; background: var(--primary); border: none; color: white; padding: 12px; border-radius: 8px; cursor: pointer; font-weight: 500; }
.form-grid .btn-send:hover:not(:disabled) { background: var(--primary-dark); }
.form-grid .btn-send:disabled { opacity: 0.5; cursor: not-allowed; }
@media (max-width: 640px) { .form-grid { grid-template-columns: 1fr; } .form-grid .form-textarea, .form-grid .btn-send { grid-column: span 1; } }

.toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.toolbar-left { display: flex; gap: 10px; flex: 1; flex-wrap: wrap; }
.toolbar-right { display: flex; gap: 8px; flex-wrap: wrap; }
.search-input { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; color: var(--text-primary); flex: 1; min-width: 180px; max-width: 300px; outline: none; }
.filter-select { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; color: var(--text-primary); outline: none; }
.toolbar-btn { background: var(--bg-card); border: 1px solid var(--border); color: var(--text-primary); padding: 9px 14px; border-radius: 8px; cursor: pointer; font-size: 13px; }
.toolbar-btn:hover:not(:disabled) { background: var(--bg-card-hover); }
.toolbar-btn.danger { color: var(--danger); }
.toolbar-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.batch-cancel { background: rgba(255,255,255,0.2); border: none; color: white; padding: 5px 12px; border-radius: 6px; cursor: pointer; }

.select-bar { background: var(--bg-card); padding: 10px 16px; border-radius: 8px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; border: 1px solid var(--border); }
.select-all-label { display: flex; align-items: center; gap: 10px; cursor: pointer; }
.select-all-label .checkbox { display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px; border: 2px solid var(--border); border-radius: 4px; font-size: 12px; color: white; background: var(--bg-dark); }
.select-all-label .checkbox.checked { background: var(--primary); border-color: var(--primary); }
.select-text { font-size: 14px; color: var(--text-primary); }
.select-bar .batch-cancel { background: var(--bg-dark); border: 1px solid var(--border); color: var(--text-primary); padding: 5px 12px; border-radius: 6px; cursor: pointer; }
.select-bar .batch-cancel:hover { background: var(--bg-card-hover); }

.tab-bar { display: flex; gap: 8px; margin-bottom: 16px; border-bottom: 1px solid var(--border); }
.tab-btn { background: none; border: none; color: var(--text-secondary); padding: 10px 16px; cursor: pointer; border-bottom: 2px solid transparent; }
.tab-btn:hover { color: var(--text-primary); }
.tab-btn.active { color: var(--primary); border-bottom-color: var(--primary); }

.cards-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
.device-card { background: var(--bg-card); border-radius: 12px; overflow: hidden; border: 2px solid transparent; }
.device-card:hover { background: var(--bg-card-hover); }
.device-card.selected { border-color: var(--primary); }
.device-card.offline { opacity: 0.75; }

.card-header { display: flex; justify-content: space-between; align-items: center; padding: 10px 14px; border-bottom: 1px solid var(--border); }
.card-checkbox { cursor: pointer; }
.checkbox { display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px; border: 2px solid var(--border); border-radius: 4px; font-size: 12px; color: transparent; }
.checkbox.checked { background: var(--primary); border-color: var(--primary); color: white; }
.card-status { font-size: 12px; padding: 3px 8px; border-radius: 4px; font-weight: 500; }
.card-status.online { background: rgba(16,185,129,0.2); color: var(--success); }
.card-status.offline { background: rgba(239,68,68,0.2); color: var(--danger); }

.card-body { padding: 14px; }
.device-name { font-size: 15px; font-weight: 600; margin-bottom: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.device-ip { font-family: monospace; font-size: 13px; color: var(--primary); margin-bottom: 3px; }
.device-mac { font-family: monospace; font-size: 11px; color: var(--text-secondary); margin-bottom: 10px; }

.sims-info { background: var(--bg-dark); border-radius: 8px; padding: 10px; margin-bottom: 10px; }
.sim-item { display: flex; align-items: center; gap: 6px; margin-bottom: 5px; }
.sim-item:last-child { margin-bottom: 0; }
.sim-label { font-size: 10px; background: var(--primary); color: white; padding: 2px 5px; border-radius: 3px; flex-shrink: 0; }
.sim-op { font-size: 11px; color: var(--text-secondary); }
.sim-signal { font-size: 11px; color: var(--success); background: rgba(48,209,88,0.15); padding: 1px 6px; border-radius: 4px; margin-left: 4px; font-weight: 500; }
.sim-num { font-family: monospace; font-size: 12px; margin-left: auto; }
.device-meta { display: flex; justify-content: space-between; font-size: 11px; color: var(--text-secondary); }

.card-actions { display: flex; border-top: 1px solid var(--border); }
.card-btn { flex: 1; background: none; border: none; color: var(--text-secondary); padding: 10px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: background 0.15s, color 0.15s; }
.card-btn:hover { background: var(--bg-card-hover); color: var(--text-primary); }
.card-btn.danger:hover { color: var(--danger); }

.empty-state { text-align: center; padding: 60px 20px; color: var(--text-secondary); }
.empty-icon { display: flex; align-items: center; justify-content: center; margin: 0 auto 12px; opacity: 0.6; }
.empty-state p { margin-bottom: 16px; }
.empty-btn { background: var(--primary); color: white; border: none; padding: 10px 24px; border-radius: 8px; cursor: pointer; }

.numbers-table { background: var(--bg-card); border-radius: 12px; overflow: hidden; }
.numbers-table table { width: 100%; border-collapse: collapse; }
.numbers-table th, .numbers-table td { padding: 12px 16px; text-align: left; border-bottom: 1px solid var(--border); }
.numbers-table th { background: var(--bg-dark); font-size: 13px; color: var(--text-secondary); font-weight: 500; }
.mono { font-family: monospace; }

.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.75); display: flex; align-items: center; justify-content: center; z-index: 1000; padding: 20px; }
.modal { background: var(--bg-card); border-radius: 16px; width: 100%; max-width: 420px; max-height: 90vh; overflow-y: auto; }
.modal-lg { max-width: 560px; }
.modal-xl { max-width: 820px; }
.modal-header { display: flex; justify-content: space-between; align-items: center; padding: 18px 20px; border-bottom: 1px solid var(--border); }
.modal-header h3 { font-size: 17px; font-weight: 600; }
.modal-close { background: none; border: none; color: var(--text-secondary); font-size: 24px; cursor: pointer; }
.modal-body { padding: 20px; display: flex; flex-direction: column; gap: 10px; }
.modal-hint { color: var(--text-secondary); font-size: 12px; line-height: 1.5; }
.modal-footer { display: flex; gap: 12px; padding: 16px 20px; border-top: 1px solid var(--border); }

.form-input, .form-textarea-full { width: 100%; background: var(--bg-dark); border: 1px solid var(--border); border-radius: 8px; padding: 11px 14px; color: var(--text-primary); font-size: 14px; outline: none; }
.form-input:focus, .form-textarea-full:focus { border-color: var(--primary); }
.form-textarea-full { resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; line-height: 1.45; }
.config-section { display: flex; flex-direction: column; gap: 8px; }
.config-intro, .config-flow { display: flex; flex-direction: column; gap: 12px; }
.config-info, .config-hint { color: var(--text-secondary); font-size: 13px; line-height: 1.6; }
.config-hint { margin-top: -2px; }
.config-devices-list { display: flex; flex-direction: column; gap: 8px; max-height: 360px; overflow-y: auto; }
.config-device-item { border: 1px solid var(--border); border-radius: 8px; overflow: hidden; background: var(--bg-dark); }
.config-device-header { display: grid; grid-template-columns: 1fr auto auto; gap: 10px; align-items: center; padding: 10px 12px; cursor: pointer; }
.config-content { border-top: 1px solid var(--border); padding: 10px; overflow-x: auto; }
.config-pre { margin: 0; white-space: pre-wrap; word-break: break-word; font-size: 12px; line-height: 1.45; color: var(--text-secondary); }
.config-error { color: var(--danger); font-size: 13px; }
.config-status { font-size: 12px; padding: 2px 8px; border-radius: 999px; }
.config-status.ok { background: rgba(16,185,129,0.2); color: var(--success); }
.config-status.warn { background: rgba(245,158,11,0.2); color: var(--warning); }
.config-status.err { background: rgba(239,68,68,0.2); color: var(--danger); }
.config-status.info { background: rgba(59,130,246,0.2); color: var(--primary); }
.config-expand-icon { color: var(--text-secondary); font-size: 12px; }
.config-regex-section { display: flex; flex-direction: column; gap: 10px; border-top: 1px solid var(--border); padding-top: 12px; }
.config-section-title { font-weight: 600; font-size: 14px; }
.config-btn-row { display: flex; gap: 10px; }
.config-btn-row .btn-cancel, .config-btn-row .btn-confirm { flex: 1; }
.config-diff { margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; line-height: 1.45; white-space: pre-wrap; word-break: break-word; }
.diff-line { padding: 1px 4px; }
.diff-add { background: rgba(16,185,129,0.14); color: var(--success); }
.diff-del { background: rgba(239,68,68,0.14); color: var(--danger); }
.diff-same { color: var(--text-secondary); }
.diff-prefix { display: inline-block; width: 18px; color: var(--text-secondary); }
.full-width { width: 100%; }

.btn-cancel { flex: 1; background: var(--bg-dark); border: 1px solid var(--border); color: var(--text-primary); padding: 11px; border-radius: 8px; cursor: pointer; }
.btn-confirm { flex: 1; background: var(--primary); border: none; color: white; padding: 11px; border-radius: 8px; cursor: pointer; font-weight: 500; }
.btn-confirm:hover:not(:disabled) { background: var(--primary-dark); }
.btn-confirm:disabled { opacity: 0.5; cursor: not-allowed; }
.danger-btn { background: var(--danger); }
.danger-btn:hover:not(:disabled) { background: #dc2626; }
.btn-preview { flex: 1; background: var(--bg-card-hover); border: 1px solid var(--border); color: var(--text-primary); padding: 11px; border-radius: 8px; cursor: pointer; font-weight: 500; }
.btn-preview:hover:not(:disabled) { background: var(--border); }
.btn-preview:disabled { opacity: 0.5; cursor: not-allowed; }

.btn-check { flex: 1; background: var(--primary); border: none; color: white; padding: 11px; border-radius: 8px; cursor: pointer; font-weight: 500; }
.btn-check:hover:not(:disabled) { background: var(--primary-dark); }
.btn-check:disabled { opacity: 0.5; cursor: not-allowed; }

.btn-upgrade { background: var(--success); border: none; color: white; padding: 11px; border-radius: 8px; font-weight: 500; cursor: pointer; }
.btn-upgrade:hover:not(:disabled) { background: #0d9668; }
.btn-upgrade:disabled { opacity: 0.5; cursor: not-allowed; }

.ota-check-section { text-align: center; padding: 30px; color: var(--text-secondary); }
.ota-check-section p { margin-bottom: 20px; }
.ota-check-section .btn-check { padding: 12px 24px; }

.ota-loading { text-align: center; padding: 30px; color: var(--text-secondary); }
.ota-summary { display: flex; gap: 16px; margin-bottom: 16px; font-size: 14px; }
.ota-updatable { color: var(--success); font-weight: 500; }
.ota-latest { color: var(--text-secondary); }
.ota-failed { color: var(--danger); }
.ota-list { max-height: 300px; overflow-y: auto; }
.ota-item { display: flex; justify-content: space-between; padding: 10px; border-bottom: 1px solid var(--border); }
.ota-item:last-child { border-bottom: none; }
.ota-item.has-update { background: rgba(16,185,129,0.1); }
.ota-item.failed { background: rgba(239,68,68,0.1); }
.ota-ip { font-family: monospace; font-size: 13px; }
.ota-version { font-size: 12px; }
.version-current { color: var(--text-secondary); }
.version-arrow { color: var(--warning); margin: 0 6px; }
.version-new { color: var(--success); font-weight: 500; }
.version-error { color: var(--danger); }

.detail-grid { display: grid; grid-template-columns: repeat(2,1fr); gap: 12px; margin-bottom: 16px; }
.detail-item { display: flex; flex-direction: column; gap: 4px; }
.detail-label { font-size: 11px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; }
.status-badge { font-size: 12px; padding: 3px 8px; border-radius: 4px; font-weight: 500; display: inline-block; }
.status-badge.online { background: rgba(16,185,129,0.2); color: var(--success); }
.status-badge.offline { background: rgba(239,68,68,0.2); color: var(--danger); }

.sim-edit-section { border-top: 1px solid var(--border); padding-top: 14px; display: flex; flex-direction: column; gap: 8px; }
.sim-edit-title { font-size: 13px; color: var(--text-secondary); margin-bottom: 2px; }

@media (max-width: 640px) {
  .header { flex-direction: column; align-items: flex-start; }
  .toolbar { flex-direction: column; }
  .toolbar-left, .toolbar-right { width: 100%; }
  .search-input { max-width: none; }
  .cards-grid { grid-template-columns: 1fr; }
  .detail-grid { grid-template-columns: 1fr; }
}
.preview-section { margin-top: 20px; border-top: 1px solid var(--border); padding-top: 20px; }
.preview-section h4 { margin-bottom: 12px; font-size: 14px; color: var(--text-primary); }
.preview-list { max-height: 200px; overflow-y: auto; border: 1px solid var(--border); border-radius: 8px; padding: 8px; }
.preview-item { display: flex; align-items: center; gap: 12px; padding: 8px 12px; border-bottom: 1px solid var(--border); }
.preview-item:last-child { border-bottom: none; }
.preview-ip { font-family: monospace; font-size: 13px; flex: 1; }
.preview-alias { color: var(--text-secondary); font-size: 12px; flex: 1; }
.preview-status { color: var(--primary); font-size: 12px; flex: 2; }
</style>