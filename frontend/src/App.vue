<template>
  <div class="app">
    <!-- 首次使用引导 -->
    <div v-if="!initialized" class="init-screen">
      <div class="init-card">
        <div class="init-icon">📱</div>
        <h1>绿邮设备管理</h1>
        <p>本地版 - 数据存储在手机</p>
        <button class="init-btn" @click="initializeApp">开始使用</button>
      </div>
    </div>

    <!-- 主界面 -->
    <div v-else class="main-app">
      <!-- 顶部导航 -->
      <header class="app-header">
        <h1>绿邮设备管理</h1>
        <div class="header-actions">
          <button class="icon-btn" @click="showScan = true">🔍</button>
          <button class="icon-btn" @click="showSettings = true">⚙️</button>
        </div>
      </header>

      <!-- 统计栏 -->
      <div class="stats-bar">
        <div class="stat-item">
          <span class="stat-value">{{ devices.length }}</span>
          <span class="stat-label">设备总数</span>
        </div>
        <div class="stat-item">
          <span class="stat-value">{{ onlineDevices.length }}</span>
          <span class="stat-label">在线设备</span>
        </div>
        <div class="stat-item">
          <span class="stat-value">{{ allNumbers.length }}</span>
          <span class="stat-label">SIM卡号码</span>
        </div>
      </div>

      <!-- 功能标签页 -->
      <div class="tabs">
        <button 
          :class="['tab', { active: activeTab === 'devices' }]" 
          @click="activeTab = 'devices'"
        >
          📱 设备列表
        </button>
        <button 
          :class="['tab', { active: activeTab === 'numbers' }]" 
          @click="activeTab = 'numbers'"
        >
          📞 号码列表
        </button>
        <button 
          :class="['tab', { active: activeTab === 'sms' }]" 
          @click="activeTab = 'sms'"
        >
          💬 发短信
        </button>
        <button 
          :class="['tab', { active: activeTab === 'call' }]" 
          @click="activeTab = 'call'"
        >
          📞 打电话
        </button>
      </div>

      <!-- 设备列表 -->
      <div v-if="activeTab === 'devices'" class="tab-content">
        <div v-if="devices.length === 0" class="empty-state">
          <div class="empty-icon">📱</div>
          <p>还没有设备</p>
          <button class="add-btn" @click="showScan = true">扫描设备</button>
        </div>

        <div v-else class="device-list">
          <div class="list-header">
            <label class="select-all">
              <input 
                type="checkbox" 
                :checked="selectedDevices.length === devices.length && devices.length > 0"
                @change="toggleSelectAll"
              />
              全选
            </label>
            <button 
              v-if="selectedDevices.length > 0" 
              class="batch-btn danger"
              @click="deleteSelected"
            >
              删除选中 ({{ selectedDevices.length }})
            </button>
          </div>

          <div class="devices">
            <div 
              v-for="device in devices" 
              :key="device.id" 
              :class="['device-card', { selected: selectedDevices.includes(device.id) }]"
            >
              <div class="device-checkbox">
                <input 
                  type="checkbox" 
                  :checked="selectedDevices.includes(device.id)"
                  @change="toggleSelect(device.id)"
                />
              </div>
              <div class="device-info" @click="selectDevice(device)">
                <div class="device-icon">📱</div>
                <div class="device-details">
                  <h3>{{ device.alias || device.devId || '未命名设备' }}</h3>
                  <p>{{ device.ip }} | {{ device.mac || '-' }}</p>
                  <div class="device-sims">
                    <span v-if="device.sim1" class="sim-tag">SIM1: {{ device.sim1 }}</span>
                    <span v-if="device.sim2" class="sim-tag">SIM2: {{ device.sim2 }}</span>
                  </div>
                </div>
              </div>
              <div class="device-actions">
                <span :class="['status-dot', device.status]"></span>
                <button class="action-btn" @click.stop="editDevice(device)">✏️</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 号码列表 -->
      <div v-if="activeTab === 'numbers'" class="tab-content">
        <div v-if="allNumbers.length === 0" class="empty-state">
          <div class="empty-icon">📞</div>
          <p>暂无号码</p>
          <p class="hint">请先扫描设备并获取 SIM 卡信息</p>
        </div>
        <div v-else class="numbers-list">
          <div v-for="num in allNumbers" :key="num.number + num.deviceId" class="number-item">
            <div class="number-icon">📞</div>
            <div class="number-info">
              <p class="number-value">{{ num.number }}</p>
              <p class="number-device">{{ num.deviceName }} (SIM{{ num.slot }})</p>
              <p class="number-operator">{{ num.operator || '未知运营商' }}</p>
            </div>
            <div class="number-actions">
              <button class="small-btn" @click="quickSms(num)">💬</button>
              <button class="small-btn" @click="quickCall(num)">📞</button>
            </div>
          </div>
        </div>
      </div>

      <!-- 发短信 -->
      <div v-if="activeTab === 'sms'" class="tab-content">
        <div class="form-panel">
          <div class="form-group">
            <label>发送号码（从设备 SIM 卡选择）</label>
            <select v-model="sms.fromNumber" class="select-input">
              <option value="">请选择发送号码</option>
              <option v-for="num in allNumbers" :key="num.number" :value="num.number">
                {{ num.number }} ({{ num.deviceName }} SIM{{ num.slot }})
              </option>
            </select>
          </div>
          <div class="form-group">
            <label>目标手机号</label>
            <input v-model="sms.toPhone" placeholder="请输入手机号" type="tel" />
          </div>
          <div class="form-group">
            <label>短信内容</label>
            <textarea v-model="sms.content" placeholder="请输入短信内容" rows="4"></textarea>
            <span class="char-count">{{ sms.content.length }}/500</span>
          </div>
          <button 
            class="send-btn" 
            @click="sendSms" 
            :disabled="!sms.fromNumber || !sms.toPhone || !sms.content"
          >
            📩 发送短信
          </button>
        </div>

        <!-- 发送记录 -->
        <div class="history-section">
          <h3>发送记录</h3>
          <div v-if="smsHistory.length === 0" class="empty-history">
            暂无记录
          </div>
          <div v-else class="history-list">
            <div v-for="record in smsHistory" :key="record.id" class="history-item">
              <div class="history-info">
                <p><strong>{{ record.fromNumber }}</strong> → <strong>{{ record.toPhone }}</strong></p>
                <p class="history-content">{{ record.content }}</p>
                <p class="history-time">{{ record.time }}</p>
              </div>
              <span :class="['status', record.status]">
                {{ record.status === 'sent' ? '✓ 已发送' : '✗ 失败' }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- 打电话 -->
      <div v-if="activeTab === 'call'" class="tab-content">
        <div class="form-panel">
          <div class="form-group">
            <label>拨打号码（从设备 SIM 卡选择）</label>
            <select v-model="call.fromNumber" class="select-input">
              <option value="">请选择拨打号码</option>
              <option v-for="num in allNumbers" :key="num.number" :value="num.number">
                {{ num.number }} ({{ num.deviceName }} SIM{{ num.slot }})
              </option>
            </select>
          </div>
          <div class="form-group">
            <label>目标电话号码</label>
            <input v-model="call.toPhone" placeholder="请输入电话号码" type="tel" />
          </div>
          <div class="form-group">
            <label>TTS 语音内容（可选）</label>
            <textarea v-model="call.ttsText" placeholder="接通后自动播放的语音内容" rows="3"></textarea>
          </div>
          <button 
            class="call-btn" 
            @click="makeCall" 
            :disabled="!call.fromNumber || !call.toPhone"
          >
            📞 拨打电话
          </button>
        </div>
      </div>

      <!-- 悬浮扫描按钮 -->
      <button class="fab" @click="showScan = true">
        🔍
      </button>

      <!-- 扫描设备弹窗 -->
      <div v-if="showScan" class="modal" @click.self="showScan = false">
        <div class="modal-card scan-modal">
          <h2>🔍 扫描设备</h2>
          <div class="form-group">
            <label>网段（CIDR）</label>
            <input v-model="scan.cidr" placeholder="192.168.1.0/24（留空自动检测）" />
          </div>
          <div class="form-group">
            <label>设备用户名</label>
            <input v-model="scan.user" placeholder="默认 admin" />
          </div>
          <div class="form-group">
            <label>设备密码</label>
            <input v-model="scan.password" type="password" placeholder="默认 admin" />
          </div>

          <div v-if="scanning" class="scan-progress">
            <div class="progress-icon">🔄</div>
            <p>{{ scanProgress }}</p>
            <div class="progress-bar">
              <div class="progress-fill" :style="{ width: scanPercent + '%' }"></div>
            </div>
            <p class="progress-stats">发现 {{ scanFound }} 台设备</p>
          </div>

          <div v-if="scanResult" class="scan-result">
            <div class="result-icon">✅</div>
            <p>{{ scanResult }}</p>
          </div>

          <div class="modal-actions">
            <button class="cancel-btn" @click="showScan = false">取消</button>
            <button class="confirm-btn" @click="startScan" :disabled="scanning">
              {{ scanning ? '扫描中...' : '开始扫描' }}
            </button>
          </div>
        </div>
      </div>

      <!-- 设备详情弹窗 -->
      <div v-if="selectedDevice" class="modal" @click.self="selectedDevice = null">
        <div class="modal-card">
          <h2>{{ selectedDevice.alias || selectedDevice.devId }}</h2>
          <div class="device-detail">
            <p><strong>设备ID:</strong> {{ selectedDevice.devId || '-' }}</p>
            <p><strong>IP 地址:</strong> {{ selectedDevice.ip }}</p>
            <p><strong>MAC 地址:</strong> {{ selectedDevice.mac || '-' }}</p>
            <p><strong>状态:</strong> 
              <span :class="['status', selectedDevice.status]">
                {{ selectedDevice.status === 'online' ? '在线' : '离线' }}
              </span>
            </p>
            <p><strong>SIM1:</strong> {{ selectedDevice.sim1 || '-' }} {{ selectedDevice.sim1Operator || '' }}</p>
            <p><strong>SIM2:</strong> {{ selectedDevice.sim2 || '-' }} {{ selectedDevice.sim2Operator || '' }}</p>
            <p><strong>固件版本:</strong> {{ selectedDevice.firmware || '-' }}</p>
            <p><strong>添加时间:</strong> {{ selectedDevice.createdAt }}</p>
          </div>
          <div class="modal-actions">
            <button class="cancel-btn" @click="selectedDevice = null">关闭</button>
          </div>
        </div>
      </div>

      <!-- 设置弹窗 -->
      <div v-if="showSettings" class="modal" @click.self="showSettings = false">
        <div class="modal-card">
          <h2>设置</h2>
          <div class="settings-list">
            <button class="settings-item" @click="refreshDevices">
              🔄 刷新设备状态
            </button>
            <button class="settings-item" @click="exportData">
              📤 导出数据
            </button>
            <button class="settings-item" @click="importData">
              📥 导入数据
            </button>
            <button class="settings-item danger" @click="clearData">
              🗑️ 清除所有数据
            </button>
          </div>
          <div class="modal-actions">
            <button class="cancel-btn" @click="showSettings = false">关闭</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'

// 状态
const initialized = ref(false)
const activeTab = ref('devices')
const devices = ref([])
const selectedDevices = ref([])
const showScan = ref(false)
const selectedDevice = ref(null)
const showSettings = ref(false)

// 扫描状态
const scanning = ref(false)
const scanProgress = ref('')
const scanFound = ref(0)
const scanPercent = ref(0)
const scanResult = ref('')

// 表单
const scan = ref({ cidr: '', user: '', password: '' })
const sms = ref({ fromNumber: '', toPhone: '', content: '' })
const call = ref({ fromNumber: '', toPhone: '', ttsText: '' })
const smsHistory = ref([])

// 计算属性
const onlineDevices = computed(() => devices.value.filter(d => d.status === 'online'))

const allNumbers = computed(() => {
  const numbers = []
  devices.value.forEach(device => {
    if (device.sim1) {
      numbers.push({
        number: device.sim1,
        deviceId: device.id,
        deviceName: device.alias || device.devId || device.ip,
        operator: device.sim1Operator || '',
        slot: 1
      })
    }
    if (device.sim2) {
      numbers.push({
        number: device.sim2,
        deviceId: device.id,
        deviceName: device.alias || device.devId || device.ip,
        operator: device.sim2Operator || '',
        slot: 2
      })
    }
  })
  return numbers
})

// 初始化
onMounted(() => {
  const saved = localStorage.getItem('lvyou_initialized')
  if (saved) {
    initialized.value = true
    loadData()
  }
})

function initializeApp() {
  localStorage.setItem('lvyou_initialized', 'true')
  initialized.value = true
  loadData()
}

function loadData() {
  const savedDevices = localStorage.getItem('lvyou_devices')
  if (savedDevices) devices.value = JSON.parse(savedDevices)
  const savedSms = localStorage.getItem('lvyou_sms_history')
  if (savedSms) smsHistory.value = JSON.parse(savedSms)
}

function saveDevices() {
  localStorage.setItem('lvyou_devices', JSON.stringify(devices.value))
}

function saveSmsHistory() {
  localStorage.setItem('lvyou_sms_history', JSON.stringify(smsHistory.value))
}

function toggleSelectAll() {
  if (selectedDevices.value.length === devices.value.length) {
    selectedDevices.value = []
  } else {
    selectedDevices.value = devices.value.map(d => d.id)
  }
}

function toggleSelect(id) {
  const idx = selectedDevices.value.indexOf(id)
  if (idx >= 0) {
    selectedDevices.value.splice(idx, 1)
  } else {
    selectedDevices.value.push(id)
  }
}

function selectDevice(device) {
  selectedDevice.value = device
}

function editDevice(device) {
  const newAlias = prompt('输入新别名', device.alias || '')
  if (newAlias !== null) {
    device.alias = newAlias
    saveDevices()
  }
}

function deleteSelected() {
  if (confirm(`确定删除 ${selectedDevices.value.length} 个设备吗？`)) {
    devices.value = devices.value.filter(d => !selectedDevices.value.includes(d.id))
    selectedDevices.value = []
    saveDevices()
  }
}

function quickSms(num) {
  sms.value.fromNumber = num.number
  activeTab.value = 'sms'
}

function quickCall(num) {
  call.value.fromNumber = num.number
  activeTab.value = 'call'
}

// 扫描（模拟）
async function startScan() {
  scanning.value = true
  scanProgress.value = '正在扫描...'
  scanFound.value = 0
  scanPercent.value = 0
  scanResult.value = ''

  const totalSteps = 10
  for (let i = 0; i < totalSteps; i++) {
    await new Promise(resolve => setTimeout(resolve, 500))
    scanPercent.value = ((i + 1) / totalSteps) * 100
    if (i === 3) scanProgress.value = '正在探测设备...'
    else if (i === 7) scanProgress.value = '正在读取 SIM 卡信息...'
    if (i === 5) scanFound.value = 2
    else if (i === 8) scanFound.value = 3
  }

  const mockDevices = [
    {
      id: Date.now().toString(),
      devId: 'LY-2024-001',
      ip: '192.168.1.101',
      mac: 'AA:BB:CC:DD:EE:01',
      alias: '一楼设备',
      status: 'online',
      sim1: '13800138001',
      sim1Operator: '中国移动',
      sim2: '13900139001',
      sim2Operator: '中国联通',
      firmware: 'v2.1.5',
      createdAt: new Date().toLocaleString()
    },
    {
      id: (Date.now() + 1).toString(),
      devId: 'LY-2024-002',
      ip: '192.168.1.102',
      mac: 'AA:BB:CC:DD:EE:02',
      alias: '二楼设备',
      status: 'online',
      sim1: '13700137001',
      sim1Operator: '中国电信',
      sim2: '',
      sim2Operator: '',
      firmware: 'v2.1.5',
      createdAt: new Date().toLocaleString()
    },
    {
      id: (Date.now() + 2).toString(),
      devId: 'LY-2024-003',
      ip: '192.168.1.103',
      mac: 'AA:BB:CC:DD:EE:03',
      alias: '三楼设备',
      status: 'offline',
      sim1: '13600136001',
      sim1Operator: '中国移动',
      sim2: '13500135001',
      sim2Operator: '中国移动',
      firmware: 'v2.1.4',
      createdAt: new Date().toLocaleString()
    }
  ]

  mockDevices.forEach(device => {
    if (!devices.value.find(d => d.ip === device.ip)) {
      devices.value.push(device)
    }
  })
  saveDevices()

  scanProgress.value = '扫描完成'
  scanResult.value = `发现 ${scanFound.value} 台设备，已添加到设备列表`

  setTimeout(() => {
    scanning.value = false
    showScan.value = false
    scanResult.value = ''
  }, 2000)
}

function refreshDevices() {
  // 模拟刷新状态
  devices.value.forEach(d => {
    d.status = Math.random() > 0.3 ? 'online' : 'offline'
  })
  saveDevices()
  alert('设备状态已刷新')
}

// 发短信
function sendSms() {
  if (!sms.value.fromNumber || !sms.value.toPhone || !sms.value.content) {
    alert('请填写完整信息')
    return
  }

  const record = {
    id: Date.now().toString(),
    fromNumber: sms.value.fromNumber,
    toPhone: sms.value.toPhone,
    content: sms.value.content,
    status: 'sent',
    time: new Date().toLocaleString()
  }

  smsHistory.value.unshift(record)
  saveSmsHistory()
  sms.value.toPhone = ''
  sms.value.content = ''
  alert('短信已发送')
}

// 打电话
function makeCall() {
  if (!call.value.fromNumber || !call.value.toPhone) {
    alert('请填写完整信息')
    return
  }
  alert(`正在使用 ${call.value.fromNumber} 拨打 ${call.value.toPhone}...`)
  call.value.toPhone = ''
  call.value.ttsText = ''
}

// 导出/导入
function exportData() {
  const data = { devices: devices.value, smsHistory: smsHistory.value, exportTime: new Date().toISOString() }
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `lvyou-backup-${new Date().toISOString().slice(0, 10)}.json`
  a.click()
  URL.revokeObjectURL(url)
}

function importData() {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.json'
  input.onchange = (e) => {
    const file = e.target.files[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (e) => {
      try {
        const data = JSON.parse(e.target.result)
        if (data.devices) { devices.value = data.devices; saveDevices() }
        if (data.smsHistory) { smsHistory.value = data.smsHistory; saveSmsHistory() }
        alert('导入成功！')
      } catch (err) { alert('导入失败：文件格式错误') }
    }
    reader.readAsText(file)
  }
  input.click()
}

function clearData() {
  if (confirm('确定清除所有数据吗？此操作不可恢复！')) {
    devices.value = []
    smsHistory.value = []
    selectedDevices.value = []
    localStorage.removeItem('lvyou_devices')
    localStorage.removeItem('lvyou_sms_history')
    alert('数据已清除')
  }
}
</script>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f0f2f5; color: #333; }

.init-screen { min-height: 100vh; display: flex; align-items: center; justify-content: center; background: linear-gradient(135deg, #1a1a2e, #16213e, #0f3460); }
.init-card { background: white; border-radius: 20px; padding: 40px; text-align: center; max-width: 350px; width: 90%; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }
.init-icon { font-size: 64px; margin-bottom: 20px; }
.init-card h1 { margin-bottom: 10px; color: #1a1a2e; }
.init-card p { color: #666; margin-bottom: 30px; }
.init-btn { background: #2e8b57; color: white; border: none; padding: 15px 40px; border-radius: 10px; font-size: 16px; font-weight: 600; cursor: pointer; width: 100%; }

.main-app { min-height: 100vh; padding-bottom: 80px; }
.app-header { background: #1a1a2e; color: white; padding: 16px 20px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; }
.app-header h1 { font-size: 18px; font-weight: 600; }
.header-actions { display: flex; gap: 8px; }
.icon-btn { background: none; border: none; font-size: 20px; cursor: pointer; padding: 8px; }

.stats-bar { display: flex; background: white; padding: 16px; gap: 16px; border-bottom: 1px solid #eee; }
.stat-item { flex: 1; text-align: center; }
.stat-value { display: block; font-size: 24px; font-weight: 700; color: #2e8b57; }
.stat-label { font-size: 12px; color: #999; }

.tabs { display: flex; background: white; border-bottom: 1px solid #eee; position: sticky; top: 56px; z-index: 99; overflow-x: auto; }
.tab { flex: 1; padding: 12px 8px; background: none; border: none; font-size: 13px; cursor: pointer; color: #666; border-bottom: 2px solid transparent; white-space: nowrap; }
.tab.active { color: #2e8b57; border-bottom-color: #2e8b57; font-weight: 600; }

.tab-content { padding: 16px; }

.empty-state { text-align: center; padding: 60px 20px; }
.empty-icon { font-size: 64px; margin-bottom: 20px; }
.empty-state p { color: #666; margin-bottom: 10px; }
.empty-state .hint { font-size: 13px; color: #999; }
.add-btn { background: #2e8b57; color: white; border: none; padding: 12px 24px; border-radius: 8px; font-size: 14px; cursor: pointer; }

.list-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.select-all { display: flex; align-items: center; gap: 8px; font-size: 14px; cursor: pointer; }
.batch-btn { padding: 8px 12px; border: none; border-radius: 6px; font-size: 12px; cursor: pointer; }
.batch-btn.danger { background: #fee; color: #d32f2f; }

.devices { display: flex; flex-direction: column; gap: 10px; }
.device-card { background: white; border-radius: 10px; padding: 14px; display: flex; align-items: center; gap: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); border: 2px solid transparent; transition: border-color 0.2s; }
.device-card.selected { border-color: #2e8b57; background: #f0faf4; }
.device-checkbox input { width: 18px; height: 18px; cursor: pointer; }
.device-info { flex: 1; display: flex; align-items: center; gap: 12px; cursor: pointer; }
.device-icon { font-size: 28px; }
.device-details h3 { font-size: 15px; margin-bottom: 3px; }
.device-details p { font-size: 12px; color: #666; margin-bottom: 4px; }
.device-sims { display: flex; gap: 6px; flex-wrap: wrap; }
.sim-tag { font-size: 11px; background: #e8f5e9; color: #2e8b57; padding: 2px 8px; border-radius: 10px; }
.device-actions { display: flex; align-items: center; gap: 8px; }
.status-dot { width: 10px; height: 10px; border-radius: 50%; }
.status-dot.online { background: #4caf50; }
.status-dot.offline { background: #ccc; }
.action-btn { background: none; border: none; font-size: 16px; cursor: pointer; padding: 6px; }

.numbers-list { display: flex; flex-direction: column; gap: 10px; }
.number-item { background: white; border-radius: 10px; padding: 14px; display: flex; align-items: center; gap: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
.number-icon { font-size: 28px; }
.number-info { flex: 1; }
.number-value { font-size: 16px; font-weight: 600; margin-bottom: 2px; }
.number-device { font-size: 12px; color: #666; }
.number-operator { font-size: 11px; color: #999; }
.number-actions { display: flex; gap: 6px; }
.small-btn { width: 36px; height: 36px; border-radius: 50%; border: none; font-size: 16px; cursor: pointer; background: #f5f5f5; }
.small-btn:hover { background: #e8e8e8; }

.form-panel { background: white; border-radius: 12px; padding: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
.form-group { margin-bottom: 16px; }
.form-group label { display: block; font-size: 13px; font-weight: 600; color: #333; margin-bottom: 6px; }
.form-group input, .form-group textarea, .select-input { width: 100%; padding: 12px; border: 2px solid #e0e0e0; border-radius: 8px; font-size: 14px; outline: none; }
.form-group input:focus, .form-group textarea:focus, .select-input:focus { border-color: #2e8b57; }
.select-input { background: white; }
.char-count { font-size: 12px; color: #999; float: right; margin-top: 4px; }
.send-btn, .call-btn { width: 100%; padding: 14px; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; }
.send-btn { background: #2e8b57; color: white; }
.call-btn { background: #007AFF; color: white; }
.send-btn:disabled, .call-btn:disabled { background: #ccc; cursor: not-allowed; }

.history-section { margin-top: 24px; }
.history-section h3 { font-size: 16px; margin-bottom: 12px; }
.empty-history { text-align: center; color: #999; padding: 20px; }
.history-list { display: flex; flex-direction: column; gap: 10px; }
.history-item { background: white; border-radius: 8px; padding: 12px; display: flex; justify-content: space-between; align-items: flex-start; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.history-info p { margin-bottom: 4px; font-size: 14px; }
.history-content { color: #666; font-size: 13px !important; }
.history-time { color: #999; font-size: 12px !important; }

.fab { position: fixed; bottom: 30px; right: 30px; width: 60px; height: 60px; border-radius: 50%; background: #2e8b57; color: white; border: none; font-size: 28px; cursor: pointer; box-shadow: 0 6px 16px rgba(46,139,87,0.5); z-index: 999; display: flex; align-items: center; justify-content: center; }

.modal { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000; padding: 20px; }
.modal-card { background: white; border-radius: 16px; padding: 24px; width: 100%; max-width: 400px; max-height: 80vh; overflow-y: auto; }
.modal-card h2 { margin-bottom: 20px; color: #1a1a2e; }
.modal-actions { display: flex; gap: 12px; margin-top: 20px; }
.cancel-btn { flex: 1; padding: 12px; background: #f0f0f0; border: none; border-radius: 8px; font-size: 14px; cursor: pointer; }
.confirm-btn { flex: 1; padding: 12px; background: #2e8b57; color: white; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; }
.confirm-btn:disabled { background: #aaa; }

.scan-modal { max-width: 500px; }
.scan-progress { text-align: center; padding: 20px 0; }
.progress-icon { font-size: 48px; animation: spin 1.5s linear infinite; margin-bottom: 10px; }
@keyframes spin { to { transform: rotate(360deg); } }
.progress-bar { height: 6px; background: #e0e0e0; border-radius: 3px; margin: 10px 0; overflow: hidden; }
.progress-fill { height: 100%; background: #2e8b57; border-radius: 3px; transition: width 0.3s; }
.progress-stats { color: #666; font-size: 14px; }
.scan-result { text-align: center; padding: 20px 0; }
.result-icon { font-size: 48px; margin-bottom: 10px; }

.device-detail p { margin-bottom: 12px; font-size: 14px; }
.status { font-size: 12px; padding: 2px 8px; border-radius: 10px; }
.status.online { background: #e6f7e6; color: #2e8b57; }
.status.offline { background: #f7e6e6; color: #d32f2f; }

.settings-list { display: flex; flex-direction: column; gap: 12px; margin-bottom: 20px; }
.settings-item { background: #f5f5f5; border: none; padding: 14px; border-radius: 10px; font-size: 14px; cursor: pointer; text-align: left; display: flex; align-items: center; gap: 10px; }
.settings-item:hover { background: #eee; }
.settings-item.danger { color: #d32f2f; }
.settings-item.danger:hover { background: #fee; }
</style>
