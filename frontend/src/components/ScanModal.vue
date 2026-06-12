<template>
  <div class="scan-modal" @click.self="$emit('close')">
    <div class="scan-card">
      <div class="scan-header">
        <h3>🔍 扫描内网设备</h3>
        <button class="close-btn" @click="$emit('close')">✕</button>
      </div>

      <!-- 扫描配置 -->
      <div v-if="phase === 'config'" class="scan-body">
        <div class="input-group">
          <label>网段 (CIDR)</label>
          <input
            v-model="cidr"
            placeholder="192.168.1.0/24（留空自动检测）"
          />
          <span class="hint">留空将自动检测本机网段</span>
        </div>

        <div class="input-group">
          <label>分组</label>
          <input v-model="group" placeholder="可选，如：一楼、仓库A" />
        </div>

        <div class="input-group">
          <label>设备用户名</label>
          <input v-model="user" placeholder="默认 admin" />
        </div>

        <div class="input-group">
          <label>设备密码</label>
          <input v-model="password" type="password" placeholder="默认 admin" />
        </div>

        <button class="scan-btn" @click="startScan" :disabled="scanning">
          {{ scanning ? '扫描中...' : '开始扫描' }}
        </button>
      </div>

      <!-- 扫描进度 -->
      <div v-if="phase === 'scanning'" class="scan-body scanning">
        <div class="progress-ring">
          <div class="ring-icon">{{ status === 'error' ? '❌' : '🔄' }}</div>
        </div>
        <div class="progress-info">
          <p class="progress-text">{{ progress || '扫描中...' }}</p>
          <div class="progress-stats">
            <span>发现: <strong>{{ found }}</strong> 台</span>
            <span>已扫描: <strong>{{ scanned }}</strong>/{{ totalIps }}</span>
          </div>
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: progressPercent + '%' }"></div>
          </div>
        </div>
        <button v-if="status === 'error'" class="scan-btn" @click="phase = 'config'">
          重新配置
        </button>
      </div>

      <!-- 扫描结果 -->
      <div v-if="phase === 'result'" class="scan-body result">
        <div class="result-icon">✅</div>
        <p class="result-text">{{ resultMessage }}</p>

        <div v-if="devices.length > 0" class="device-list">
          <div v-for="dev in devices" :key="dev.ip" class="device-item">
            <span class="device-icon">📱</span>
            <div class="device-info">
              <span class="device-id">{{ dev.devId || dev.ip }}</span>
              <span class="device-ip">{{ dev.ip }}</span>
            </div>
          </div>
        </div>

        <button class="scan-btn" @click="$emit('close')">完成</button>
        <button class="scan-btn secondary" @click="phase = 'config'; devices = []">
          继续扫描
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'
import { api } from '../api/client'

const emit = defineEmits(['close', 'scanned'])

const phase = ref('config') // config | scanning | result
const cidr = ref('')
const group = ref('')
const user = ref('')
const password = ref('')
const scanning = ref(false)
const progress = ref('')
const status = ref('')
const found = ref(0)
const scanned = ref(0)
const totalIps = ref(0)
const resultMessage = ref('')
const devices = ref([])

let pollTimer = null

const progressPercent = computed(() => {
  if (totalIps.value <= 0) return 0
  return Math.min(100, Math.round((scanned.value / totalIps.value) * 100))
})

async function startScan() {
  scanning.value = true
  phase.value = 'scanning'
  progress.value = '正在提交扫描任务...'
  status.value = ''
  found.value = 0
  scanned.value = 0
  totalIps.value = 0

  try {
    const resp = await api.post('/api/scan/start', {
      cidr: cidr.value.trim() || null,
      group: group.value.trim() || null,
      user: user.value.trim(),
      password: password.value.trim()
    })

    if (resp.data?.ok && resp.data?.scanId) {
      pollStatus(resp.data.scanId)
    } else {
      status.value = 'error'
      progress.value = '启动扫描失败'
      scanning.value = false
    }
  } catch (e) {
    status.value = 'error'
    progress.value = e.response?.data?.detail || '启动扫描失败'
    scanning.value = false
  }
}

function pollStatus(scanId) {
  if (pollTimer) clearInterval(pollTimer)

  pollTimer = setInterval(async () => {
    try {
      const resp = await api.get(`/api/scan/status/${scanId}`)
      const data = resp.data

      progress.value = data.progress || ''
      status.value = data.status || ''
      found.value = data.found || 0
      scanned.value = data.scanned || 0
      totalIps.value = data.total_ips || 0

      if (data.status === 'done') {
        clearInterval(pollTimer)
        pollTimer = null
        scanning.value = false
        phase.value = 'result'
        devices.value = data.devices || []
        resultMessage.value = data.progress || `扫描完成，发现 ${found.value} 台设备`
        emit('scanned')
      } else if (data.status === 'error') {
        clearInterval(pollTimer)
        pollTimer = null
        scanning.value = false
        resultMessage.value = data.progress || '扫描出错'
      }
    } catch (e) {
      // 网络错误，继续轮询
    }
  }, 1500)
}

onUnmounted(() => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
</script>

<style scoped>
.scan-modal {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 16px;
}

.scan-card {
  background: white;
  border-radius: 16px;
  width: 100%;
  max-width: 420px;
  max-height: 90vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0,0,0,0.3);
}

.scan-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px;
  border-bottom: 1px solid #eee;
}

.scan-header h3 {
  margin: 0;
  font-size: 18px;
  color: #1a1a2e;
}

.close-btn {
  background: none;
  border: none;
  font-size: 20px;
  cursor: pointer;
  color: #999;
  padding: 4px 8px;
}

.scan-body {
  padding: 20px;
}

.input-group {
  margin-bottom: 16px;
}

.input-group label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: #333;
  margin-bottom: 6px;
}

.input-group input {
  width: 100%;
  padding: 11px 13px;
  border: 2px solid #e0e0e0;
  border-radius: 10px;
  font-size: 14px;
  outline: none;
  box-sizing: border-box;
  transition: border-color 0.2s;
}

.input-group input:focus {
  border-color: #2e8b57;
}

.hint {
  font-size: 12px;
  color: #999;
  margin-top: 4px;
  display: block;
}

.scan-btn {
  width: 100%;
  padding: 13px;
  background: #2e8b57;
  color: white;
  border: none;
  border-radius: 10px;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  margin-top: 8px;
  transition: background 0.2s;
}

.scan-btn:disabled {
  background: #aaa;
  cursor: not-allowed;
}

.scan-btn:not(:disabled):hover {
  background: #246b47;
}

.scan-btn.secondary {
  background: #f0f0f0;
  color: #333;
  margin-top: 8px;
}

.scan-btn.secondary:hover {
  background: #e0e0e0;
}

/* 扫描进度 */
.scanning {
  text-align: center;
  padding: 30px 20px;
}

.progress-ring {
  margin-bottom: 16px;
}

.ring-icon {
  font-size: 48px;
  animation: spin 1.5s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.progress-info {
  margin-bottom: 20px;
}

.progress-text {
  font-size: 14px;
  color: #555;
  margin: 0 0 12px;
}

.progress-stats {
  display: flex;
  justify-content: center;
  gap: 24px;
  font-size: 13px;
  color: #777;
  margin-bottom: 12px;
}

.progress-stats strong {
  color: #2e8b57;
}

.progress-bar {
  height: 6px;
  background: #e0e0e0;
  border-radius: 3px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: #2e8b57;
  border-radius: 3px;
  transition: width 0.5s ease;
}

/* 结果 */
.result {
  text-align: center;
  padding: 20px;
}

.result-icon {
  font-size: 48px;
  margin-bottom: 12px;
}

.result-text {
  font-size: 14px;
  color: #555;
  margin: 0 0 16px;
}

.device-list {
  text-align: left;
  max-height: 200px;
  overflow-y: auto;
  margin-bottom: 16px;
  border: 1px solid #eee;
  border-radius: 10px;
}

.device-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid #f5f5f5;
}

.device-item:last-child {
  border-bottom: none;
}

.device-icon {
  font-size: 20px;
}

.device-info {
  display: flex;
  flex-direction: column;
}

.device-id {
  font-size: 14px;
  font-weight: 600;
  color: #333;
}

.device-ip {
  font-size: 12px;
  color: #999;
}
</style>
