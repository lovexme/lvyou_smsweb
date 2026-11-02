<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal">
      <div class="modal-header">
        <h3>📋 设备详情</h3>
        <button class="modal-close" @click="emit('close')">×</button>
      </div>
      <div class="modal-body">
        <div class="detail-grid">
          <div class="detail-item"><span class="detail-label">设备ID</span><span>{{ device.devId || '-' }}</span></div>
          <div class="detail-item"><span class="detail-label">别名</span><span>{{ device.alias || '-' }}</span></div>
          <div class="detail-item"><span class="detail-label">IP 地址</span><span class="mono">{{ device.ip }}</span></div>
          <div class="detail-item"><span class="detail-label">MAC 地址</span><span class="mono">{{ device.mac || '-' }}</span></div>
          <div class="detail-item"><span class="detail-label">分组</span><span>{{ device.grp || 'auto' }}</span></div>
          <div class="detail-item">
            <span class="detail-label">状态</span>
            <span :class="['status-badge', device.status]">
              {{ device.status === 'online' ? '在线' : '离线' }}
            </span>
          </div>
          <div class="detail-item"><span class="detail-label">SIM1 号码</span><span class="mono">{{ device.sim1number || '-' }}</span></div>
          <div class="detail-item"><span class="detail-label">SIM1 运营商</span><span>{{ device.sim1operator || '-' }}</span></div>
          <div class="detail-item"><span class="detail-label">SIM2 号码</span><span class="mono">{{ device.sim2number || '-' }}</span></div>
          <div class="detail-item"><span class="detail-label">SIM2 运营商</span><span>{{ device.sim2operator || '-' }}</span></div>
          <div class="detail-item"><span class="detail-label">WiFi 名称</span><span>{{ device.wifiName || '-' }}</span></div>
          <div class="detail-item"><span class="detail-label">信号强度</span><span :style="{ color: wifiDbmColor(device.wifiDbm) }">{{ wifiDbmLabel(device.wifiDbm) }}</span></div>
        </div>
        <div class="sim-edit-section">
          <p class="sim-edit-title">编辑 SIM 卡号</p>
          <input :value="device.sim1number || ''" class="form-input" placeholder="SIM1 号码" @input="emit('update-sim1', $event.target.value)" />
          <input :value="device.sim2number || ''" class="form-input" placeholder="SIM2 号码" @input="emit('update-sim2', $event.target.value)" />
          <div class="sim-edit-actions">
            <button class="btn-confirm" :disabled="loading" @click="emit('save')">保存卡号</button>
            <button class="btn-secondary" :disabled="loading" @click="emit('detect')">自动识别卡号</button>
          </div>
          <p class="sim-edit-hint">多数运营商的卡不存本机号码，读不到时手动填写即可。</p>
        </div>
        <div class="sim-edit-section">
          <p class="sim-edit-title">备注（套餐、月租、用途等）</p>
          <textarea
            :value="device.note || ''"
            class="form-input note-input"
            rows="3"
            maxlength="300"
            placeholder="例如：移动 19 元 100G，主收验证码"
            @input="emit('update-note', $event.target.value)"
          ></textarea>
          <button class="btn-confirm" :disabled="loading" @click="emit('save-note')">保存备注</button>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn-cancel" @click="emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  detail: { type: Object, required: true },
  loading: { type: Boolean, default: false }
})

const emit = defineEmits(['close', 'save', 'detect', 'update-sim1', 'update-sim2', 'update-note', 'save-note'])

const device = computed(() => props.detail.device || {})

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
</script>
