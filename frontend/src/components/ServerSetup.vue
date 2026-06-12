<template>
  <div class="server-setup">
    <div class="setup-card">
      <div class="setup-icon">📡</div>
      <h2>连接服务器</h2>
      <p class="setup-desc">请输入绿邮设备管理系统的内网地址</p>
      
      <div class="input-group">
        <label>服务器地址</label>
        <input
          v-model="serverUrl"
          type="url"
          placeholder="http://192.168.1.100:8000"
          :disabled="connecting"
          @keyup.enter="connect"
        />
        <span class="hint">格式：http://IP:端口</span>
      </div>

      <div v-if="error" class="error-msg">{{ error }}</div>
      
      <button
        class="connect-btn"
        :disabled="!serverUrl.trim() || connecting"
        @click="connect"
      >
        <span v-if="connecting" class="spinner"></span>
        {{ connecting ? '连接中...' : '连接' }}
      </button>

      <div class="setup-tips">
        <p>💡 提示：</p>
        <ul>
          <li>确保手机和服务器在同一局域网</li>
          <li>默认端口：8000</li>
          <li>如果连接失败，请检查服务器防火墙设置</li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const emit = defineEmits(['connected'])

const serverUrl = ref('')
const connecting = ref(false)
const error = ref('')

onMounted(() => {
  const saved = localStorage.getItem('lvyou_server_url')
  if (saved) {
    serverUrl.value = saved
  }
})

async function connect() {
  let url = serverUrl.value.trim().replace(/\/+$/, '')
  if (!url) return

  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    error.value = '地址必须以 http:// 或 https:// 开头'
    return
  }

  // 确保地址格式正确
  if (!url.includes(':', 8)) {
    url += ':8000'
  }

  connecting.value = true
  error.value = ''

  try {
    // 测试连接
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 5000)
    
    const resp = await fetch(`${url}/api/health`, {
      signal: controller.signal
    })
    clearTimeout(timeout)
    
    if (resp.ok) {
      // 连接成功，保存地址
      localStorage.setItem('lvyou_server_url', url)
      emit('connected', url)
    } else {
      error.value = `服务器返回错误 (${resp.status})`
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      error.value = '连接超时，请检查地址和网络'
    } else {
      error.value = '无法连接到服务器，请检查地址和网络'
    }
  } finally {
    connecting.value = false
  }
}
</script>

<style scoped>
.server-setup {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
  padding: 20px;
  z-index: 9999;
  color: #333;
}

.setup-card {
  background: white;
  border-radius: 16px;
  padding: 32px 24px;
  width: 100%;
  max-width: 400px;
  text-align: center;
  box-shadow: 0 20px 60px rgba(0,0,0,0.3);
}

.setup-icon {
  font-size: 48px;
  margin-bottom: 12px;
}

h2 {
  margin: 0 0 8px;
  color: #1a1a2e;
  font-size: 22px;
}

.setup-desc {
  color: #666;
  margin: 0 0 24px;
  font-size: 14px;
}

.input-group {
  text-align: left;
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
  padding: 12px 14px;
  border: 2px solid #e0e0e0;
  border-radius: 10px;
  font-size: 15px;
  outline: none;
  transition: border-color 0.2s;
  box-sizing: border-box;
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

.error-msg {
  background: #fff3f3;
  color: #d32f2f;
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
  margin-bottom: 12px;
  text-align: left;
}

.connect-btn {
  width: 100%;
  padding: 14px;
  background: #2e8b57;
  color: white;
  border: none;
  border-radius: 10px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: background 0.2s;
}

.connect-btn:disabled {
  background: #aaa;
  cursor: not-allowed;
}

.connect-btn:not(:disabled):hover {
  background: #246b47;
}

.spinner {
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.setup-tips {
  margin-top: 24px;
  text-align: left;
  background: #f5f7fa;
  padding: 14px;
  border-radius: 10px;
  font-size: 13px;
  color: #555;
}

.setup-tips p {
  margin: 0 0 8px;
  font-weight: 600;
}

.setup-tips ul {
  margin: 0;
  padding-left: 18px;
}

.setup-tips li {
  margin-bottom: 4px;
}
</style>
