<template>
  <div class="stats-grid">
    <div class="stat-card online">
      <div class="stat-icon" aria-hidden="true"><span class="stat-dot stat-dot-online"></span></div>
      <div class="stat-info">
        <div class="stat-value">{{ online }}</div>
        <div class="stat-label">在线</div>
      </div>
      <div ref="onlineChart" class="sparkline"></div>
    </div>
    <div class="stat-card offline">
      <div class="stat-icon" aria-hidden="true"><span class="stat-dot stat-dot-offline"></span></div>
      <div class="stat-info">
        <div class="stat-value">{{ offline }}</div>
        <div class="stat-label">离线</div>
      </div>
      <div ref="offlineChart" class="sparkline"></div>
    </div>
    <div class="stat-card total">
      <div class="stat-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M17 2H7a2 2 0 00-2 2v16a2 2 0 002 2h10a2 2 0 002-2V4a2 2 0 00-2-2zm-5 19a1 1 0 110-2 1 1 0 010 2zm5-4H7V5h10v12z"/></svg>
      </div>
      <div class="stat-info">
        <div class="stat-value">{{ total }}</div>
        <div class="stat-label">设备</div>
      </div>
      <div ref="totalChart" class="sparkline"></div>
    </div>
    <div class="stat-card sim">
      <div class="stat-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M19 3H9l-6 6v10a2 2 0 002 2h14a2 2 0 002-2V5a2 2 0 00-2-2zm-2 14H7v-2h10v2zm0-4H7v-2h10v2zm0-4h-4V6h4v3z"/></svg>
      </div>
      <div class="stat-info">
        <div class="stat-value">{{ simCount }}</div>
        <div class="stat-label">SIM卡</div>
      </div>
      <div ref="simChart" class="sparkline"></div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, nextTick } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([LineChart, GridComponent, CanvasRenderer])

const props = defineProps({
  online: { type: Number, default: 0 },
  offline: { type: Number, default: 0 },
  total: { type: Number, default: 0 },
  simCount: { type: Number, default: 0 },
  trendData: {
    type: Object,
    default: () => ({
      online: [65, 72, 68, 75, 80, 78, 82],
      offline: [15, 12, 14, 10, 8, 10, 6],
      total: [80, 84, 82, 85, 88, 88, 88],
      sim: [160, 168, 164, 170, 176, 176, 176]
    })
  }
})

const onlineChart = ref(null)
const offlineChart = ref(null)
const totalChart = ref(null)
const simChart = ref(null)

function getThemeColor() {
  const isDark = document.documentElement.classList.contains('dark-theme') ||
    !document.documentElement.classList.contains('light-theme')
  return {
    line: isDark ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.15)',
    area: isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.03)'
  }
}

function initChart(el, data, color) {
  if (!el) return
  const chart = echarts.init(el)
  const theme = getThemeColor()
  chart.setOption({
    grid: { top: 8, bottom: 8, left: 0, right: 0 },
    xAxis: { show: false, type: 'category', data: data.map((_, i) => i) },
    yAxis: { show: false, type: 'value' },
    series: [{
      type: 'line',
      data: data,
      smooth: true,
      symbol: 'none',
      lineStyle: { color, width: 2 },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: color.replace(')', ', 0.2)').replace('rgb', 'rgba') },
          { offset: 1, color: 'transparent' }
        ])
      }
    }]
  })
  return chart
}

let charts = []

function initCharts() {
  charts.forEach(c => c?.dispose())
  charts = [
    initChart(onlineChart.value, props.trendData.online, '#22c55e'),
    initChart(offlineChart.value, props.trendData.offline, '#ef4444'),
    initChart(totalChart.value, props.trendData.total, '#3b82f6'),
    initChart(simChart.value, props.trendData.sim, '#f59e0b')
  ]
}

onMounted(() => {
  nextTick(() => initCharts())
  window.addEventListener('resize', () => charts.forEach(c => c?.resize()))
})

watch(() => props.trendData, () => {
  nextTick(() => initCharts())
}, { deep: true })
</script>

<style scoped>
.stats-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 16px; margin-bottom: 24px; }
@media (max-width: 768px) { .stats-grid { grid-template-columns: repeat(2,1fr); } }
.stat-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 22px;
  display: flex;
  align-items: center;
  gap: 16px;
  transition: all var(--transition);
  position: relative;
  overflow: hidden;
}
.stat-card:hover {
  border-color: var(--border-light);
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}
.stat-icon { display: flex; align-items: center; justify-content: center; width: 48px; height: 48px; border-radius: var(--radius-md); transition: transform var(--transition); flex-shrink: 0; }
.stat-icon svg { width: 22px; height: 22px; }
.stat-card:hover .stat-icon { transform: scale(1.08); }
.stat-card.total .stat-icon { background: rgba(59,130,246,0.12); color: var(--primary); }
.stat-card.sim .stat-icon { background: rgba(245,158,11,0.12); color: var(--warning); }
.stat-card.online .stat-icon { background: rgba(34,197,94,0.12); }
.stat-card.offline .stat-icon { background: rgba(239,68,68,0.12); }
.stat-dot { display: block; width: 10px; height: 10px; border-radius: 50%; }
.stat-dot-online { background: var(--success); animation: pulse-dot 2s ease-in-out infinite; box-shadow: 0 0 0 4px var(--success-glow); }
.stat-dot-offline { background: var(--danger); box-shadow: 0 0 0 4px var(--danger-glow); }
@keyframes pulse-dot {
  0% { transform: scale(1); }
  50% { transform: scale(1.15); }
  100% { transform: scale(1); }
}
.stat-info { flex: 1; min-width: 0; }
.stat-value { font-size: 30px; font-weight: 800; letter-spacing: -0.03em; }
.stat-label { font-size: 13px; color: var(--text-muted); margin-top: 2px; }
.stat-card.online .stat-value { color: var(--success); }
.stat-card.offline .stat-value { color: var(--danger); }
.stat-card.total .stat-value { color: var(--primary); }
.stat-card.sim .stat-value { color: var(--warning); }
.sparkline { width: 80px; height: 40px; flex-shrink: 0; }
</style>
