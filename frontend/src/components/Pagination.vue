<template>
  <div v-if="pages > 1 || total > pageSize" class="pagination">
    <span class="pagination-info">共 {{ total }} 条 · 第 {{ page }} / {{ pages || 1 }} 页</span>
    <div class="pagination-controls">
      <button
        class="pg-btn"
        :disabled="page <= 1"
        @click="emit('change', 1)"
      >首页</button>
      <button
        class="pg-btn"
        :disabled="page <= 1"
        @click="emit('change', page - 1)"
      >上一页</button>
      <span class="pg-current">{{ page }}</span>
      <button
        class="pg-btn"
        :disabled="page >= pages"
        @click="emit('change', page + 1)"
      >下一页</button>
      <button
        class="pg-btn"
        :disabled="page >= pages"
        @click="emit('change', pages)"
      >末页</button>
    </div>
  </div>
</template>

<script setup>
// FIX(P2#7): minimal pagination control. Stays simple on purpose -- we
// don't render every page number because the dashboard rarely sees more
// than a few pages, and the home/end + prev/next combo is enough for the
// 99% case. If page-jump becomes necessary we'll add an input here.
defineProps({
  page: { type: Number, required: true },
  pages: { type: Number, default: 0 },
  pageSize: { type: Number, default: 100 },
  total: { type: Number, default: 0 }
})
const emit = defineEmits(['change'])
</script>

<style scoped>
.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  margin-top: 18px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md, 10px);
  flex-wrap: wrap;
  gap: 12px;
}
.pagination-info { color: var(--text-muted, #64748b); font-size: 13px; }
.pagination-controls { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.pg-btn {
  background: var(--bg-input, #0f1629);
  border: 1px solid var(--border);
  color: var(--text-primary, #f1f5f9);
  padding: 6px 14px;
  border-radius: var(--radius-sm, 6px);
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}
.pg-btn:hover:not(:disabled) { background: var(--primary); color: white; border-color: var(--primary); transform: translateY(-1px); }
.pg-btn:disabled { opacity: 0.35; cursor: not-allowed; }
.pg-current {
  background: var(--primary);
  color: white;
  border-radius: var(--radius-sm, 6px);
  padding: 6px 14px;
  font-size: 13px;
  font-weight: 600;
  box-shadow: 0 0 12px rgba(59, 130, 246, 0.3);
}
</style>
