<script setup>
import { ref, computed, onMounted } from 'vue'
import axios from 'axios'

const api = axios.create({ baseURL: '' })

const TOKEN_KEY = 'board_mgr_token'
const TOKEN_EXPIRES_KEY = 'board_mgr_token_expires'

const uiPass = ref('')
const authed = ref(false)
const loading = ref(false)
const notice = ref({ text: '', type: 'info' })

const devices = ref([])
const numbers = ref([])

const activeTab = ref('devices')
const searchText = ref('')
const groupFilter = ref('all')

const fromSelected = ref('')
const toPhone = ref('')
const content = ref('')

const commMode = ref('sms')
const dialPhone = ref('')
const ttsText = ref('')

const selectedIds = ref([])
const selectAll = ref(false)

const showWifiModal = ref(false)
const showDetailModal = ref(false)
const showUpgradeModal = ref(false)
const showConfigModal = ref(false)

const wifiSsid = ref('')
const wifiPwd = ref('')
const deviceDetail = ref(null)

const configStep = ref('read')
const configData = ref([])
const configPattern = ref('')
const configReplacement = ref('')
const configFlags = ref('')
const configPreviewData = ref([])
const configExpandedIds = ref([])

const scanCidr = ref('')
const scanUser = ref('admin')
const scanPass = ref('admin')
const scanGroup = ref('')
const scanning = ref(false)

function setNotice(text, type = 'info') {
  notice.value = { text, type }
}

function clearNotice() {
  notice.value = { text: '', type: 'info' }
}

function setToken(token) {
  api.defaults.headers.common.Authorization = 'Bearer ' + token
}

function clearToken() {
  delete api.defaults.headers.common.Authorization
}

function saveAuth(token, expiresIn) {
  const expiresAt = Date.now() + (expiresIn * 1000)
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(TOKEN_EXPIRES_KEY, String(expiresAt))
  setToken(token)
}

function clearStoredAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(TOKEN_EXPIRES_KEY)
  clearToken()
}

function restoreAuth() {
  const token = localStorage.getItem(TOKEN_KEY)
  const expiresAt = parseInt(localStorage.getItem(TOKEN_EXPIRES_KEY) || '0', 10)
  if (!token || !expiresAt || Date.now() >= expiresAt) {
    clearStoredAuth()
    return false
  }
  setToken(token)
  return true
}

// FIX: 登录页新增 HTTP 429 频率限制错误提示
async function login() {
  const password = uiPass.value.trim()
  if (!password) {
    setNotice('请输入密码', 'err')
    return
  }
  loading.value = true
  clearNotice()
  try {
    const response = await api.post('/api/login', { username: 'admin', password })
    const data = response.data || {}
    saveAuth(data.token, data.expiresIn || 28800)
    authed.value = true
    uiPass.value = ''
    setNotice('登录成功', 'ok')
    await refresh()
  } catch (e) {
    authed.value = false
    clearStoredAuth()
    const status = e && e.response && e.response.status
    const detail = e && e.response && e.response.data && e.response.data.detail
    if (status === 429) {
      setNotice(detail || '登录尝试过于频繁，请稍后再试', 'err')
    } else if (status === 401) {
      setNotice('密码错误，请重试', 'err')
    } else {
      setNotice(detail || '连接失败，请检查服务是否运行', 'err')
    }
  } finally {
    loading.value = false
  }
}

async function logout(showMsg = false) {
  try {
    await api.post('/api/logout')
  } catch {
    // ignore
  }
  authed.value = false
  uiPass.value = ''
  clearStoredAuth()
  selectedIds.value = []
  selectAll.value = false
  if (showMsg) {
    setNotice('已退出登录', 'info')
  } else {
    clearNotice()
  }
}

api.interceptors.response.use(
  response => response,
  async error => {
    if (error && error.response && error.response.status === 401 && authed.value) {
      await logout()
      setNotice('登录已失效，请重新输入密码', 'err')
    }
    return Promise.reject(error)
  }
)

onMounted(async () => {
  if (!restoreAuth()) return
  loading.value = true
  try {
    await api.get('/api/health')
    authed.value = true
    await refresh()
  } catch {
    await logout()
    setNotice('登录已过期，请重新登录', 'err')
  } finally {
    loading.value = false
  }
})

const uniqueGroups = computed(() => {
  const groupSet = new Set(['all'])
  devices.value.forEach(device => {
    if (device.grp) groupSet.add(device.grp)
  })
  return Array.from(groupSet)
})

const onlineCount = computed(() => devices.value.filter(device => device.status === 'online').length)
const offlineCount = computed(() => devices.value.filter(device => device.status !== 'online').length)
const selectedCount = computed(() => selectedIds.value.length)

const filteredDevices = computed(() => {
  return devices.value.filter(device => {
    const keyword = searchText.value.toLowerCase()
    const matchSearch = !keyword ||
      (device.ip || '').toLowerCase().includes(keyword) ||
      (device.mac || '').toLowerCase().includes(keyword) ||
      (device.devId || '').toLowerCase().includes(keyword) ||
      (device.alias || '').toLowerCase().includes(keyword) ||
      (device.sims && device.sims.sim1 && device.sims.sim1.number || '').includes(keyword) ||
      (device.sims && device.sims.sim2 && device.sims.sim2.number || '').includes(keyword) ||
      (device.sims && device.sims.sim1 && device.sims.sim1.operator || '').toLowerCase().includes(keyword) ||
      (device.sims && device.sims.sim2 && device.sims.sim2.operator || '').toLowerCase().includes(keyword)
    const matchGroup = groupFilter.value === 'all' || device.grp === groupFilter.value
    return matchSearch && matchGroup
  })
})

const filteredNumbers = computed(() => {
  return numbers.value.filter(item => {
    const keyword = searchText.value.toLowerCase()
    return !keyword ||
      (item.number || '').includes(keyword) ||
      (item.operator || '').toLowerCase().includes(keyword) ||
      (item.deviceName || '').toLowerCase().includes(keyword)
  })
})

function displayName(device) {
  return (device.alias || '').trim() || device.devId || device.ip
}

function prettyTime(ts) {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

async function refresh() {
  loading.value = true
  try {
    const result = await Promise.all([
      api.get('/api/devices'),
      api.get('/api/numbers')
    ])
    const devData = result[0].data
    devices.value = Array.isArray(devData) ? devData : (devData.items || [])
    const numData = result[1].data
    numbers.value = Array.isArray(numData) ? numData : (numData.items || [])
  } catch (e) {
    if (!(e && e.response && e.response.status === 401)) {
      setNotice('获取数据失败，请检查网络连接', 'err')
    }
  } finally {
    loading.value = false
  }
}

function toggleSelectAll() {
  const isAllSelected = selectedCount.value === filteredDevices.value.length && filteredDevices.value.length > 0
  selectedIds.value = isAllSelected ? [] : filteredDevices.value.map(device => device.id)
}

// FIX: 凭据改为 POST Body；用 completed 标志防止超时误判
async function startScanAdd() {
  scanning.value = true
  setNotice('正在提交扫描任务...', 'info')
  try {
    const scanResp = await api.post('/api/scan/start', {
      cidr:     scanCidr.value  || undefined,
      group:    scanGroup.value || undefined,
      user:     scanUser.value,
      password: scanPass.value
    })
    const scanId = scanResp.data && scanResp.data.scanId
    if (!scanId) {
      setNotice('扫描任务创建失败', 'err')
      scanning.value = false
      return
    }
    setNotice('扫描进行中，请稍候...', 'info')
    let completed = false
    for (let i = 0; i < 60; i++) {
      await new Promise(resolve => setTimeout(resolve, 2000))
      try {
        const statusResp = await api.get('/api/scan/status/' + scanId)
        const st = statusResp.data || {}
        const progress = st.progress || ''
        if (st.status === 'done') {
          completed = true
          setNotice(`扫描完成，发现 ${st.found} 台设备`, st.found ? 'ok' : 'warn')
          await refresh()
          break
        } else if (st.status === 'error') {
          completed = true
          setNotice(progress || '扫描出错', 'err')
          break
        } else if (progress) {
          setNotice(`扫描中: ${progress}`, 'info')
        }
      } catch {
        // 状态查询失败，继续重试
      }
    }
    // FIX: 只在确实未完成时才提示超时
    if (!completed) {
      setNotice('扫描超时，设备可能稍后出现，可点一次刷新确认', 'warn')
      await refresh()
    }
  } catch (e) {
    const detail = e && e.response && e.response.data && e.response.data.detail
    setNotice(detail || '扫描启动失败，请检查网络连接', 'err')
  } finally {
    scanning.value = false
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
    await api.post('/api/sms/send-direct', {
      deviceId: sender.deviceId,
      phone: toPhone.value,
      content: content.value,
      slot: sender.slot
    })
    setNotice('短信已发送', 'ok')
    toPhone.value = ''
    content.value = ''
  } catch (e) {
    const status = e && e.response && e.response.status
    const detail = e && e.response && e.response.data && e.response.data.detail
    if (status === 429) {
      setNotice(detail || '发送过于频繁，请稍后再试', 'err')
    } else if (status === 400) {
      setNotice(detail || '参数错误，请检查手机号和内容', 'err')
    } else {
      setNotice(detail || '发送失败，请检查网络和设备状态', 'err')
    }
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
    setNotice('请选择有效的发送卡号', 'err')
    return
  }
  loading.value = true
  try {
    await api.post('/api/tel/dial', {
      deviceId: sender.deviceId,
      slot: sender.slot,
      phone: dialPhone.value,
      tts: ttsText.value
    })
    setNotice('拨号已执行', 'ok')
    dialPhone.value = ''
    ttsText.value = ''
  } catch (e) {
    const status = e && e.response && e.response.status
    const detail = e && e.response && e.response.data && e.response.data.detail
    if (status === 429) {
      setNotice(detail || '拨号过于频繁，请稍后再试', 'err')
    } else {
      setNotice(detail || '拨号失败，请检查网络和设备状态', 'err')
    }
  } finally {
    loading.value = false
  }
}

async function renameDevice(device) {
  const name = prompt('请输入设备别名：', device.alias || '')
  if (name === null) return
  try {
    await api.post('/api/devices/' + device.id + '/alias', { alias: name })
    setNotice('已更新别名', 'ok')
    await refresh()
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || '更新失败', 'err')
  }
}

async function setGroup(device) {
  const group = prompt('请输入分组名称：', device.grp || 'auto')
  if (group === null) return
  try {
    await api.post('/api/devices/' + device.id + '/group', { group })
    setNotice('已更新分组', 'ok')
    await refresh()
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || '更新失败', 'err')
  }
}

async function deleteDevice(device) {
  if (!confirm('确认删除设备 ' + displayName(device) + '？')) return
  loading.value = true
  try {
    await api.delete('/api/devices/' + device.id)
    setNotice('已删除', 'ok')
    await refresh()
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || '删除失败', 'err')
  } finally {
    loading.value = false
  }
}

function toggleSelect(id) {
  const idx = selectedIds.value.indexOf(id)
  if (idx > -1) {
    selectedIds.value.splice(idx, 1)
  } else {
    selectedIds.value.push(id)
  }
}

function isSelected(id) {
  return selectedIds.value.includes(id)
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
}

async function applyWifi() {
  if (!wifiSsid.value.trim()) {
    setNotice('请输入SSID', 'err')
    return
  }
  loading.value = true
  try {
    const response = await api.post('/api/devices/batch/wifi', {
      device_ids: selectedIds.value,
      ssid: wifiSsid.value.trim(),
      pwd: wifiPwd.value.trim()
    })
    const list = response.data && response.data.results ? response.data.results : []
    const okCount = list.filter(item => item.ok).length
    setNotice('WiFi 添加完成：' + okCount + '/' + list.length, okCount ? 'ok' : 'err')
    closeWifiModal()
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || e.message || '配置失败', 'err')
  } finally {
    loading.value = false
  }
}

async function batchDeleteSelected() {
  if (!selectedCount.value) {
    setNotice('请先勾选设备', 'err')
    return
  }
  if (!confirm('确认删除所选 ' + selectedCount.value + ' 台设备？')) return
  loading.value = true
  try {
    const response = await api.post('/api/devices/batch/delete', { device_ids: selectedIds.value })
    const deleted = response.data && response.data.deleted ? response.data.deleted : 0
    setNotice('删除完成：' + deleted + '/' + selectedCount.value, deleted ? 'ok' : 'warn')
    selectedIds.value = []
    selectAll.value = false
    await refresh()
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || e.message || '删除失败', 'err')
  } finally {
    loading.value = false
  }
}

async function showDetail(device) {
  loading.value = true
  try {
    const response = await api.get('/api/devices/' + device.id + '/detail')
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
    await api.post('/api/devices/' + id + '/sim', {
      sim1: deviceDetail.value.device.sim1number || '',
      sim2: deviceDetail.value.device.sim2number || ''
    })
    setNotice('已保存卡号', 'ok')
    await refresh()
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || e.message || '保存失败', 'err')
  } finally {
    loading.value = false
  }
}

function wifiDbmColor(dbm) {
  const v = parseInt(dbm, 10)
  if (isNaN(v)) return 'var(--text-secondary)'
  if (v >= 60) return 'var(--success)'
  if (v >= 30) return 'var(--warning)'
  return 'var(--danger)'
}

function wifiDbmLabel(dbm) {
  const v = parseInt(dbm, 10)
  if (isNaN(v) || !dbm) return '-'
  if (v >= 60) return `${dbm} (强)`
  if (v >= 30) return `${dbm} (中)`
  return `${dbm} (弱)`
}

async function checkOtaForDevice() {
  const id = deviceDetail.value && deviceDetail.value.device && deviceDetail.value.device.id
  if (!id) return
  loading.value = true
  try {
    const resp = await api.get('/api/devices/' + id + '/ota/check')
    const data = resp.data || {}
    if (data.hasUpdate) {
      setNotice(`发现新版本: v${data.newVer}`, 'ok')
    } else {
      setNotice('当前已是最新版本', 'info')
    }
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || '检查更新失败', 'err')
  } finally {
    loading.value = false
  }
}

function openUpgradeModal() {
  if (!selectedCount.value) {
    setNotice('请先勾选设备', 'err')
    return
  }
  showUpgradeModal.value = true
}

function closeUpgradeModal() {
  showUpgradeModal.value = false
}

async function applyUpgrade() {
  if (!selectedCount.value) return
  if (!confirm(`确认对 ${selectedCount.value} 台设备检查并升级？\n有新版本的设备将自动升级并重启。`)) return
  loading.value = true
  try {
    const resp = await api.post('/api/devices/batch/upgrade', {
      device_ids: selectedIds.value
    })
    const results = resp.data && resp.data.results || []
    const okCount = results.filter(r => r.ok && !r.skipped).length
    const skippedCount = results.filter(r => r.skipped).length
    const failCount = results.filter(r => !r.ok).length
    let msg = `升级完成: ${okCount} 台已升级`
    if (skippedCount) msg += `, ${skippedCount} 台已是最新`
    if (failCount) msg += `, ${failCount} 台失败`
    setNotice(msg, failCount ? 'warn' : 'ok')
    closeUpgradeModal()
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || '升级失败', 'err')
  } finally {
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
  configFlags.value = ''
  configPreviewData.value = []
  configExpandedIds.value = []
  showConfigModal.value = true
}

function closeConfigModal() {
  showConfigModal.value = false
  configStep.value = 'read'
  configData.value = []
  configPattern.value = ''
  configReplacement.value = ''
  configFlags.value = ''
  configPreviewData.value = []
  configExpandedIds.value = []
}

async function readConfigs() {
  loading.value = true
  try {
    const resp = await api.post('/api/devices/batch/config/read', {
      device_ids: selectedIds.value
    })
    configData.value = resp.data && resp.data.configs || []
    configStep.value = 'edit'
    if (configData.value.length > 0) {
      configExpandedIds.value = [configData.value[0].id]
    }
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
    const resp = await api.post('/api/devices/batch/config/preview', {
      device_ids: selectedIds.value,
      pattern: configPattern.value,
      replacement: configReplacement.value,
      flags: configFlags.value
    })
    configPreviewData.value = resp.data && resp.data.previews || []
    configStep.value = 'preview'
    if (configPreviewData.value.length > 0) {
      configExpandedIds.value = [configPreviewData.value[0].id]
    }
  } catch (e) {
    setNotice((e && e.response && e.response.data && e.response.data.detail) || '预览失败', 'err')
  } finally {
    loading.value = false
  }
}

async function writeConfigs() {
  if (!confirm(`确认对 ${selectedCount.value} 台设备写入配置？\n此操作不可撤销，请确认预览结果正确。`)) return
  loading.value = true
  try {
    const resp = await api.post('/api/devices/batch/config/write', {
      device_ids: selectedIds.value,
      pattern: configPattern.value,
      replacement: configReplacement.value,
      flags: configFlags.value
    })
    const results = resp.data && resp.data.results || []
    const okCount = results.filter(r => r.ok).length
    const changedCount = results.filter(r => r.changed).length
    setNotice(`配置写入完成: ${okCount}/${results.length} 成功, ${changedCount} 台有变更`, okCount ? 'ok' : 'err')
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
</script>

<template>
  <div class="app">
    <div v-if="!authed" class="login-container">
      <div class="login-box">
        <div class="login-icon">📱</div>
        <h1 class="login-title">绿邮X系列内网群控</h1>
        <p class="login-subtitle">请输入密码登录</p>
        <div class="login-form">
          <input
            v-model="uiPass"
            class="login-input"
            type="password"
            placeholder="请输入密码"
            @keyup.enter="login"
            autocomplete="current-password"
          />
          <button class="login-button" :disabled="loading" @click="login">
            <span v-if="loading">验证中...</span>
            <span v-else>登 录</span>
          </button>
        </div>
        <div v-if="notice.text" class="login-notice" :class="'notice-' + notice.type">
          {{ notice.text }}
        </div>
      </div>
    </div>

    <div v-else class="main-container">
      <header class="header">
        <div class="header-left">
          <div class="logo">📱</div>
          <div class="header-title">
            <h1>绿邮X系列内网群控</h1>
            <p>Token 登录版</p>
          </div>
        </div>
        <div class="header-right">
          <button class="header-btn primary" @click="startScanAdd" :disabled="scanning">
            🔍 {{ scanning ? '扫描中...' : '扫描' }}
          </button>
          <button class="header-btn" @click="refresh" :disabled="loading">🔄 刷新</button>
          <button class="header-btn logout" @click="logout(true)">退出</button>
        </div>
      </header>

      <div v-if="notice.text" class="notice-bar" :class="'notice-' + notice.type">
        <span>{{ notice.text }}</span>
        <button class="notice-close" @click="clearNotice">×</button>
      </div>

      <div class="stats-grid">
        <div class="stat-card online">
          <div class="stat-icon">🟢</div>
          <div class="stat-info">
            <div class="stat-value">{{ onlineCount }}</div>
            <div class="stat-label">在线</div>
          </div>
        </div>
        <div class="stat-card offline">
          <div class="stat-icon">🔴</div>
          <div class="stat-info">
            <div class="stat-value">{{ offlineCount }}</div>
            <div class="stat-label">离线</div>
          </div>
        </div>
        <div class="stat-card total">
          <div class="stat-icon">📱</div>
          <div class="stat-info">
            <div class="stat-value">{{ devices.length }}</div>
            <div class="stat-label">设备</div>
          </div>
        </div>
        <div class="stat-card sim">
          <div class="stat-icon">💳</div>
          <div class="stat-info">
            <div class="stat-value">{{ numbers.length }}</div>
            <div class="stat-label">SIM卡</div>
          </div>
        </div>
      </div>

      <div class="sms-section">
        <div class="section-header">
          <h2>📤 消息发送</h2>
          <div class="mode-tabs">
            <button :class="['mode-tab', { active: commMode === 'sms' }]" @click="commMode = 'sms'">短信</button>
            <button :class="['mode-tab', { active: commMode === 'dial' }]" @click="commMode = 'dial'">拨号</button>
          </div>
        </div>

        <div v-show="commMode === 'sms'" class="form-grid">
          <select v-model="fromSelected" class="form-select">
            <option value="">选择发送卡号</option>
            <option
              v-for="n in numbers"
              :key="String(n.deviceId) + '-' + String(n.slot)"
              :value="String(n.deviceId) + '|' + String(n.slot)"
            >
              {{ n.number }} ({{ n.operator || '未知' }})
            </option>
          </select>
          <input v-model="toPhone" class="form-input" placeholder="收件人号码" />
          <textarea v-model="content" class="form-textarea" placeholder="短信内容..." rows="2"></textarea>
          <button class="btn-send" :disabled="loading || !fromSelected || !toPhone || !content" @click="send">📨 发送</button>
        </div>

        <div v-show="commMode === 'dial'" class="form-grid">
          <select v-model="fromSelected" class="form-select">
            <option value="">选择发送卡号</option>
            <option
              v-for="n in numbers"
              :key="String(n.deviceId) + '-' + String(n.slot)"
              :value="String(n.deviceId) + '|' + String(n.slot)"
            >
              {{ n.number }} ({{ n.operator || '未知' }})
            </option>
          </select>
          <input v-model="dialPhone" class="form-input" placeholder="拨打的号码" />
          <textarea v-model="ttsText" class="form-textarea" placeholder="TTS内容（可选）..." rows="2"></textarea>
          <button class="btn-send" :disabled="loading || !fromSelected || !dialPhone" @click="dial">📞 拨号</button>
        </div>
      </div>

      <div class="toolbar">
        <div class="toolbar-left">
          <input v-model="searchText" class="search-input" placeholder="🔍 搜索设备/IP/MAC/号码..." />
          <select v-model="groupFilter" class="filter-select">
            <option value="all">全部分组</option>
            <option v-for="g in uniqueGroups.filter(x => x !== 'all')" :key="g" :value="g">{{ g }}</option>
          </select>
        </div>
        <div class="toolbar-right">
          <button class="toolbar-btn" @click="openWifiModal" :disabled="selectedCount === 0">📶 WiFi</button>
          <button class="toolbar-btn" @click="openUpgradeModal" :disabled="selectedCount === 0">⬆️ 升级</button>
          <button class="toolbar-btn" @click="openConfigModal" :disabled="selectedCount === 0">⚙️ 配置</button>
          <button class="toolbar-btn danger" @click="batchDeleteSelected" :disabled="selectedCount === 0">🗑️ 删除</button>
        </div>
      </div>

      <div class="select-bar">
        <label class="select-all-label">
          <span :class="['checkbox', { checked: selectedCount > 0 && selectedCount === filteredDevices.length }]">
            {{ selectedCount > 0 && selectedCount === filteredDevices.length ? '✓' : (selectedCount > 0 ? '−' : '') }}
          </span>
          <input
            type="checkbox"
            :checked="selectedCount === filteredDevices.length && filteredDevices.length > 0"
            :indeterminate="selectedCount > 0 && selectedCount < filteredDevices.length"
            @change="toggleSelectAll"
            style="display: none"
          />
          <span class="select-text">
            {{ selectedCount > 0 ? `已选择 ${selectedCount} 台` : '全选' }}
          </span>
        </label>
        <button v-if="selectedCount > 0" class="batch-cancel" @click="selectedIds = []; selectAll = false">取消选择</button>
      </div>

      <div class="tab-bar">
        <button :class="['tab-btn', { active: activeTab === 'devices' }]" @click="activeTab = 'devices'">
          📱 设备列表 ({{ filteredDevices.length }})
        </button>
        <button :class="['tab-btn', { active: activeTab === 'numbers' }]" @click="activeTab = 'numbers'">
          💳 号码列表 ({{ filteredNumbers.length }})
        </button>
      </div>

      <div v-if="activeTab === 'devices'" class="cards-grid">
        <div v-if="filteredDevices.length === 0" class="empty-state">
          <div class="empty-icon">📡</div>
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
                <span class="sim-num">{{ d.sims.sim1.number || '-' }}</span>
              </div>
              <div v-if="d.sims && d.sims.sim2 && (d.sims.sim2.number || d.sims.sim2.operator)" class="sim-item">
                <span class="sim-label">SIM2</span>
                <span class="sim-op">{{ d.sims.sim2.operator || '-' }}</span>
                <span class="sim-num">{{ d.sims.sim2.number || '-' }}</span>
              </div>
            </div>

            <div class="device-meta">
              <span class="device-group">{{ d.grp || 'auto' }}</span>
              <span class="device-time">{{ prettyTime(d.lastSeen) }}</span>
            </div>
          </div>

          <div class="card-actions">
            <button class="card-btn" @click="showDetail(d)" title="详情">👁️</button>
            <button class="card-btn" @click="renameDevice(d)" title="改名">✏️</button>
            <button class="card-btn" @click="setGroup(d)" title="分组">🏷️</button>
            <button class="card-btn danger" @click="deleteDevice(d)" title="删除">🗑️</button>
          </div>
        </div>
      </div>

      <div v-if="activeTab === 'numbers'" class="numbers-table">
        <div v-if="filteredNumbers.length === 0" class="empty-state">
          <div class="empty-icon">💳</div>
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
      </div>

      <div v-if="showWifiModal" class="modal-overlay" @click.self="closeWifiModal">
        <div class="modal">
          <div class="modal-header">
            <h3>📶 批量配置 WiFi</h3>
            <button class="modal-close" @click="closeWifiModal">×</button>
          </div>
          <div class="modal-body">
            <input v-model="wifiSsid" class="form-input" placeholder="WiFi 名称 (SSID)" />
            <input v-model="wifiPwd" class="form-input" type="password" placeholder="WiFi 密码" autocomplete="off" />
          </div>
          <div class="modal-footer">
            <button class="btn-cancel" @click="closeWifiModal">取消</button>
            <button class="btn-confirm" @click="applyWifi" :disabled="loading">确认配置</button>
          </div>
        </div>
      </div>

      <div v-if="showUpgradeModal" class="modal-overlay" @click.self="closeUpgradeModal">
        <div class="modal">
          <div class="modal-header">
            <h3>⬆️ 批量 OTA 升级</h3>
            <button class="modal-close" @click="closeUpgradeModal">×</button>
          </div>
          <div class="modal-body">
            <p class="config-info">将对 {{ selectedCount }} 台设备检查更新，有新版本的设备将自动升级并重启。</p>
          </div>
          <div class="modal-footer">
            <button class="btn-cancel" @click="closeUpgradeModal">取消</button>
            <button class="btn-confirm" @click="applyUpgrade" :disabled="loading">检查并升级</button>
          </div>
        </div>
      </div>

      <div v-if="showConfigModal" class="modal-overlay" @click.self="closeConfigModal">
        <div class="modal modal-lg">
          <div class="modal-header">
            <h3>⚙️ 批量设备配置</h3>
            <button class="modal-close" @click="closeConfigModal">×</button>
          </div>
          <div class="modal-body">
            <!-- 步骤1: 读取 -->
            <div v-if="configStep === 'read'">
              <p class="config-info">将读取 {{ selectedCount }} 台设备的配置文件内容。</p>
              <button class="btn-confirm" @click="readConfigs" :disabled="loading" style="width:100%">📖 读取配置</button>
            </div>

            <!-- 步骤2: 编辑正则 -->
            <div v-if="configStep === 'edit'">
              <div class="config-devices-list">
                <div v-for="c in configData" :key="c.id" class="config-device-item">
                  <div class="config-device-header" @click="toggleConfigExpand(c.id)">
                    <span>{{ c.ip }}</span>
                    <span :class="['config-status', c.ok ? 'ok' : 'err']">{{ c.ok ? '✓' : '✗' }}</span>
                    <span class="config-expand-icon">{{ configExpandedIds.includes(c.id) ? '▼' : '▶' }}</span>
                  </div>
                  <div v-if="configExpandedIds.includes(c.id)" class="config-content">
                    <pre v-if="c.ok" class="config-pre">{{ c.config }}</pre>
                    <span v-else class="config-error">{{ c.error }}</span>
                  </div>
                </div>
              </div>
              <div class="config-regex-section">
                <p class="config-section-title">正则替换规则</p>
                <input v-model="configPattern" class="form-input" placeholder="正则表达式 (pattern)" />
                <input v-model="configReplacement" class="form-input" placeholder="替换文本 (replacement)" />
                <input v-model="configFlags" class="form-input" placeholder="标志位 (如 i=忽略大小写, m=多行, s=点号匹配换行)" />
                <div class="config-btn-row">
                  <button class="btn-cancel" @click="configStep = 'read'">上一步</button>
                  <button class="btn-confirm" @click="previewConfig" :disabled="loading || !configPattern.trim()">预览替换</button>
                </div>
              </div>
            </div>

            <!-- 步骤3: 预览 -->
            <div v-if="configStep === 'preview'">
              <div class="config-devices-list">
                <div v-for="p in configPreviewData" :key="p.id" class="config-device-item">
                  <div class="config-device-header" @click="toggleConfigExpand(p.id)">
                    <span>{{ p.ip }}</span>
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
                <button class="btn-confirm danger-btn" @click="writeConfigs" :disabled="loading">确认写入</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div v-if="showDetailModal && deviceDetail" class="modal-overlay" @click.self="closeDetailModal">
        <div class="modal">
          <div class="modal-header">
            <h3>📋 设备详情</h3>
            <button class="modal-close" @click="closeDetailModal">×</button>
          </div>
          <div class="modal-body">
            <div class="detail-grid">
              <div class="detail-item"><span class="detail-label">设备ID</span><span>{{ deviceDetail.device && deviceDetail.device.devId || '-' }}</span></div>
              <div class="detail-item"><span class="detail-label">别名</span><span>{{ deviceDetail.device && deviceDetail.device.alias || '-' }}</span></div>
              <div class="detail-item"><span class="detail-label">IP 地址</span><span class="mono">{{ deviceDetail.device && deviceDetail.device.ip }}</span></div>
              <div class="detail-item"><span class="detail-label">MAC 地址</span><span class="mono">{{ deviceDetail.device && deviceDetail.device.mac || '-' }}</span></div>
              <div class="detail-item"><span class="detail-label">分组</span><span>{{ deviceDetail.device && deviceDetail.device.grp || 'auto' }}</span></div>
              <div class="detail-item">
                <span class="detail-label">状态</span>
                <span :class="['status-badge', deviceDetail.device && deviceDetail.device.status]">
                  {{ deviceDetail.device && deviceDetail.device.status === 'online' ? '在线' : '离线' }}
                </span>
              </div>
              <div class="detail-item"><span class="detail-label">SIM1 号码</span><span class="mono">{{ deviceDetail.device && deviceDetail.device.sim1number || '-' }}</span></div>
              <div class="detail-item"><span class="detail-label">SIM1 运营商</span><span>{{ deviceDetail.device && deviceDetail.device.sim1operator || '-' }}</span></div>
              <div class="detail-item"><span class="detail-label">SIM2 号码</span><span class="mono">{{ deviceDetail.device && deviceDetail.device.sim2number || '-' }}</span></div>
              <div class="detail-item"><span class="detail-label">SIM2 运营商</span><span>{{ deviceDetail.device && deviceDetail.device.sim2operator || '-' }}</span></div>
              <div class="detail-item"><span class="detail-label">WiFi 名称</span><span>{{ deviceDetail.device && deviceDetail.device.wifiName || '-' }}</span></div>
              <div class="detail-item"><span class="detail-label">信号强度</span><span :style="{ color: wifiDbmColor(deviceDetail.device && deviceDetail.device.wifiDbm) }">{{ wifiDbmLabel(deviceDetail.device && deviceDetail.device.wifiDbm) }}</span></div>
            </div>
            <div class="detail-actions">
              <button class="btn-confirm" @click="checkOtaForDevice" :disabled="loading">🔍 检查更新</button>
            </div>
            <div class="sim-edit-section">
              <p class="sim-edit-title">编辑 SIM 卡号</p>
              <input v-model="deviceDetail.device.sim1number" class="form-input" placeholder="SIM1 号码" />
              <input v-model="deviceDetail.device.sim2number" class="form-input" placeholder="SIM2 号码" />
              <button class="btn-confirm" @click="saveSimSingle" :disabled="loading">保存卡号</button>
            </div>
          </div>
          <div class="modal-footer">
            <button class="btn-cancel" @click="closeDetailModal">关闭</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style>
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

:root {
  --primary: #3b82f6;
  --primary-dark: #2563eb;
  --success: #10b981;
  --danger: #ef4444;
  --warning: #f59e0b;
  --bg-dark: #0f172a;
  --bg-card: #1e293b;
  --bg-card-hover: #334155;
  --text-primary: #f1f5f9;
  --text-secondary: #94a3b8;
  --border: #334155;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg-dark);
  color: var(--text-primary);
  min-height: 100vh;
}

.login-container { display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 20px; }
.login-box { background: var(--bg-card); border-radius: 16px; padding: 40px; width: 100%; max-width: 380px; text-align: center; }
.login-icon { font-size: 48px; margin-bottom: 16px; }
.login-title { font-size: 22px; font-weight: 600; margin-bottom: 8px; }
.login-subtitle { color: var(--text-secondary); font-size: 13px; margin-bottom: 24px; }
.login-form { display: flex; flex-direction: column; gap: 12px; }
.login-input { background: var(--bg-dark); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; font-size: 16px; color: var(--text-primary); outline: none; }
.login-input:focus { border-color: var(--primary); }
.login-button { background: var(--primary); color: white; border: none; border-radius: 8px; padding: 14px; font-size: 16px; font-weight: 500; cursor: pointer; }
.login-button:hover:not(:disabled) { background: var(--primary-dark); }
.login-button:disabled { opacity: 0.6; cursor: not-allowed; }
.login-notice { margin-top: 14px; padding: 10px 14px; border-radius: 8px; font-size: 14px; }

.notice-ok { background: rgba(16,185,129,0.15); color: var(--success); }
.notice-err { background: rgba(239,68,68,0.15); color: var(--danger); }
.notice-info { background: rgba(59,130,246,0.15); color: var(--primary); }
.notice-warn { background: rgba(245,158,11,0.15); color: var(--warning); }

.main-container { padding: 20px; max-width: 1400px; margin: 0 auto; }

.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 16px; }
.header-left { display: flex; align-items: center; gap: 12px; }
.logo { font-size: 32px; }
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
.stat-icon { font-size: 32px; }
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

.batch-bar { background: var(--primary); color: white; padding: 10px 16px; border-radius: 8px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }
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
.sim-num { font-family: monospace; font-size: 12px; margin-left: auto; }
.device-meta { display: flex; justify-content: space-between; font-size: 11px; color: var(--text-secondary); }

.card-actions { display: flex; border-top: 1px solid var(--border); }
.card-btn { flex: 1; background: none; border: none; color: var(--text-secondary); padding: 10px; cursor: pointer; font-size: 15px; }
.card-btn:hover { background: var(--bg-card-hover); color: var(--text-primary); }
.card-btn.danger:hover { color: var(--danger); }

.empty-state { text-align: center; padding: 60px 20px; color: var(--text-secondary); }
.empty-icon { font-size: 56px; margin-bottom: 12px; opacity: 0.5; }
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
.modal-header { display: flex; justify-content: space-between; align-items: center; padding: 18px 20px; border-bottom: 1px solid var(--border); }
.modal-header h3 { font-size: 17px; font-weight: 600; }
.modal-close { background: none; border: none; color: var(--text-secondary); font-size: 24px; cursor: pointer; }
.modal-body { padding: 20px; display: flex; flex-direction: column; gap: 10px; }
.modal-footer { display: flex; gap: 12px; padding: 16px 20px; border-top: 1px solid var(--border); }

.form-input, .form-select-full { width: 100%; background: var(--bg-dark); border: 1px solid var(--border); border-radius: 8px; padding: 11px 14px; color: var(--text-primary); font-size: 14px; outline: none; }
.form-input:focus, .form-select-full:focus { border-color: var(--primary); }
.form-group { display: flex; flex-direction: column; gap: 6px; }
.form-label { font-size: 13px; color: var(--text-secondary); }
.config-section { display: flex; flex-direction: column; gap: 8px; }

.btn-cancel { flex: 1; background: var(--bg-dark); border: 1px solid var(--border); color: var(--text-primary); padding: 11px; border-radius: 8px; cursor: pointer; }
.btn-confirm { flex: 1; background: var(--primary); border: none; color: white; padding: 11px; border-radius: 8px; cursor: pointer; font-weight: 500; }
.btn-confirm:hover:not(:disabled) { background: var(--primary-dark); }
.btn-confirm:disabled { opacity: 0.5; cursor: not-allowed; }

.detail-grid { display: grid; grid-template-columns: repeat(2,1fr); gap: 12px; margin-bottom: 16px; }
.detail-item { display: flex; flex-direction: column; gap: 4px; }
.detail-label { font-size: 11px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; }
.status-badge { font-size: 12px; padding: 3px 8px; border-radius: 4px; font-weight: 500; display: inline-block; }
.status-badge.online { background: rgba(16,185,129,0.2); color: var(--success); }
.status-badge.offline { background: rgba(239,68,68,0.2); color: var(--danger); }

.sim-edit-section { border-top: 1px solid var(--border); padding-top: 14px; display: flex; flex-direction: column; gap: 8px; }
.sim-edit-title { font-size: 13px; color: var(--text-secondary); margin-bottom: 2px; }

.detail-actions { display: flex; gap: 8px; padding: 10px 0; border-top: 1px solid var(--border); margin-top: 8px; }
.detail-actions .btn-confirm { padding: 8px 16px; font-size: 13px; }

.radio-group { display: flex; flex-direction: column; gap: 10px; margin: 12px 0; }
.radio-label { display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 14px; color: var(--text-primary); }
.radio-label input[type="radio"] { accent-color: var(--primary); width: 16px; height: 16px; }

.config-info { font-size: 13px; color: var(--text-secondary); margin-bottom: 12px; }
.config-section-title { font-size: 13px; color: var(--text-secondary); margin-bottom: 6px; }
.config-btn-row { display: flex; gap: 10px; margin-top: 10px; }

.config-devices-list { display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px; max-height: 300px; overflow-y: auto; }
.config-device-item { border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.config-device-header { display: flex; align-items: center; gap: 10px; padding: 10px 14px; cursor: pointer; background: var(--bg-dark); font-size: 13px; }
.config-device-header:hover { background: var(--bg-card-hover); }
.config-expand-icon { font-size: 10px; color: var(--text-secondary); margin-left: auto; }
.config-status { font-size: 11px; padding: 2px 6px; border-radius: 3px; }
.config-status.ok { background: rgba(16,185,129,0.2); color: var(--success); }
.config-status.err { background: rgba(239,68,68,0.2); color: var(--danger); }
.config-status.warn { background: rgba(245,158,11,0.2); color: var(--warning); }
.config-content { padding: 10px 14px; }
.config-pre { font-family: monospace; font-size: 11px; white-space: pre-wrap; word-break: break-all; color: var(--text-primary); max-height: 200px; overflow-y: auto; line-height: 1.5; }
.config-error { font-size: 12px; color: var(--danger); }

.config-regex-section { border-top: 1px solid var(--border); padding-top: 14px; display: flex; flex-direction: column; gap: 8px; }

.config-diff { font-family: monospace; font-size: 11px; line-height: 1.6; white-space: pre-wrap; word-break: break-all; max-height: 300px; overflow-y: auto; }
.diff-line { padding: 1px 0; }
.diff-prefix { display: inline-block; width: 16px; font-weight: bold; }
.diff-same { color: var(--text-secondary); }
.diff-add { color: var(--success); background: rgba(16,185,129,0.1); }
.diff-del { color: var(--danger); background: rgba(239,68,68,0.1); text-decoration: line-through; }

.danger-btn { background: var(--danger) !important; }
.danger-btn:hover:not(:disabled) { background: #dc2626 !important; }

@media (max-width: 640px) {
  .header { flex-direction: column; align-items: flex-start; }
  .toolbar { flex-direction: column; }
  .toolbar-left, .toolbar-right { width: 100%; }
  .search-input { max-width: none; }
  .cards-grid { grid-template-columns: 1fr; }
  .detail-grid { grid-template-columns: 1fr; }
}
</style>