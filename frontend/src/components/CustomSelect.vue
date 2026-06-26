<template>
  <div class="custom-select" ref="selectRef">
    <div class="select-trigger" @click="toggle" :class="{ open: isOpen }">
      <span class="select-value">{{ selectedLabel || placeholder }}</span>
      <span class="select-arrow">{{ isOpen ? '▲' : '▼' }}</span>
    </div>
    <div class="select-dropdown" v-if="isOpen">
      <div
        class="select-option"
        :class="{ active: !value }"
        @click="select('')"
      >
        {{ placeholder }}
      </div>
      <div
        v-for="opt in options"
        :key="opt.value"
        class="select-option"
        :class="{ active: value === opt.value }"
        @click="select(opt.value)"
      >
        {{ opt.label }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  options: { type: Array, default: () => [] },
  value: { type: String, default: '' },
  placeholder: { type: String, default: '请选择' }
})

const emit = defineEmits(['update:value'])

const isOpen = ref(false)
const selectRef = ref(null)

const selectedLabel = computed(() => {
  const opt = props.options.find(o => o.value === props.value)
  return opt ? opt.label : ''
})

function toggle() {
  isOpen.value = !isOpen.value
}

function select(val) {
  emit('update:value', val)
  isOpen.value = false
}

function handleClickOutside(e) {
  if (selectRef.value && !selectRef.value.contains(e.target)) {
    isOpen.value = false
  }
}

onMounted(() => document.addEventListener('click', handleClickOutside))
onBeforeUnmount(() => document.removeEventListener('click', handleClickOutside))
</script>

<style scoped>
.custom-select {
  position: relative;
  width: 100%;
}

.select-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--bg-input, #0f1629);
  border: 1px solid var(--border, #1e293b);
  border-radius: var(--radius-md, 10px);
  padding: 12px 14px;
  cursor: pointer;
  font-size: 14px;
  color: var(--text-primary, #f1f5f9);
  transition: border-color 0.2s cubic-bezier(0.4, 0, 0.2, 1), box-shadow 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

.select-trigger:hover {
  border-color: var(--border-light, #334155);
}

.select-trigger.open {
  border-color: var(--primary, #3b82f6);
  box-shadow: 0 0 0 3px var(--primary-glow, rgba(59, 130, 246, 0.25));
}

.select-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.select-arrow {
  font-size: 10px;
  color: var(--text-muted, #64748b);
  margin-left: 8px;
  flex-shrink: 0;
  transition: transform 0.2s;
}

.select-trigger.open .select-arrow {
  transform: rotate(180deg);
}

.select-dropdown {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  background: var(--bg-card, #1a2035);
  border: 1px solid var(--border, #1e293b);
  border-radius: var(--radius-md, 10px);
  max-height: 240px;
  overflow-y: auto;
  z-index: 100;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
  animation: slideUp 0.15s ease-out;
}

@keyframes slideUp {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}

.select-option {
  padding: 10px 14px;
  cursor: pointer;
  font-size: 14px;
  color: var(--text-primary, #f1f5f9);
  transition: background 0.1s;
}

.select-option:hover {
  background: var(--bg-card-hover, #243050);
}

.select-option.active {
  background: var(--primary, #3b82f6);
  color: #fff;
}
</style>
