<template>
  <div class="skeleton" :class="{ 'skeleton-animated': animated }">
    <div v-if="type === 'card'" class="skeleton-card">
      <div class="skeleton-header">
        <div class="skeleton-circle"></div>
        <div class="skeleton-text" style="width: 60%"></div>
      </div>
      <div class="skeleton-body">
        <div class="skeleton-text" style="width: 80%"></div>
        <div class="skeleton-text" style="width: 60%"></div>
        <div class="skeleton-text" style="width: 40%"></div>
      </div>
      <div class="skeleton-footer">
        <div class="skeleton-text" style="width: 50%"></div>
      </div>
    </div>
    <div v-else-if="type === 'table'" class="skeleton-table">
      <div class="skeleton-row" v-for="i in rows" :key="i">
        <div class="skeleton-text" style="width: 20%"></div>
        <div class="skeleton-text" style="width: 30%"></div>
        <div class="skeleton-text" style="width: 25%"></div>
        <div class="skeleton-text" style="width: 25%"></div>
      </div>
    </div>
    <div v-else class="skeleton-text" :style="{ width: width, height: height }"></div>
  </div>
</template>

<script setup>
defineProps({
  type: { type: String, default: 'text' },
  animated: { type: Boolean, default: true },
  rows: { type: Number, default: 5 },
  width: { type: String, default: '100%' },
  height: { type: String, default: '20px' }
})
</script>

<style scoped>
.skeleton { display: flex; flex-direction: column; gap: 12px; }
.skeleton-animated .skeleton-text,
.skeleton-animated .skeleton-circle {
  background: linear-gradient(90deg, var(--bg-input) 25%, var(--bg-card-hover) 50%, var(--bg-input) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
}
@keyframes shimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}
.skeleton-text {
  height: 16px;
  background: var(--bg-input);
  border-radius: 4px;
  margin-bottom: 8px;
}
.skeleton-text:last-child { margin-bottom: 0; }
.skeleton-circle {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: var(--bg-input);
  flex-shrink: 0;
}
.skeleton-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 20px;
}
.skeleton-header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.skeleton-body { display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px; }
.skeleton-footer { display: flex; justify-content: flex-end; }
.skeleton-table { background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 20px; }
.skeleton-row { display: flex; gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--border); }
.skeleton-row:last-child { border-bottom: none; }
</style>
