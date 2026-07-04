<template>
  <div class="numbers-view">
    <AppHeader
      :loading="loading"
      :scanning="scanning"
      :is-dark="isDark"
      @scan="startScanAdd"
      @refresh="refresh"
      @logout="handleLogout"
      @toggle-theme="toggleTheme"
      @open-settings="$router.push('/settings')"
    />

    <div class="toolbar">
      <div class="toolbar-left">
        <input v-model="searchText" class="search-input" placeholder="搜索号码/设备/IP..." />
        <select v-model="groupFilter" class="filter-select">
          <option value="all">全部分组</option>
          <option v-for="g in uniqueGroups.filter(x => x !== 'all')" :key="g" :value="g">{{ g }}</option>
        </select>
      </div>
    </div>

    <div class="tab-bar">
      <button :class="['tab-btn', { active: false }]" @click="$router.push('/dashboard')">
        设备列表 ({{ devicesStore.devicesTotal }})
      </button>
      <button :class="['tab-btn', { active: true }]">
        号码列表 ({{ devicesStore.numbersTotal }})
      </button>
    </div>

    <NumbersTable />

    <Pagination
      :page="devicesStore.numbersPage"
      :pages="devicesStore.numbersPages"
      :page-size="devicesStore.numbersPageSize"
      :total="devicesStore.numbersTotal"
      @change="devicesStore.setNumbersPage"
    />
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useAuthStore, useDevicesStore, useScanStore } from '../stores'
import AppHeader from '../components/AppHeader.vue'
import NumbersTable from '../components/NumbersTable.vue'
import Pagination from '../components/Pagination.vue'
import { useLoading } from '../composables/useLoading'
import { useDeviceActions } from '../composables/useDeviceActions'
import { useTheme } from '../composables/useTheme'

const router = useRouter()
const authStore = useAuthStore()
const devicesStore = useDevicesStore()
const scanStore = useScanStore()

const { searchText, groupFilter, uniqueGroups } = storeToRefs(devicesStore)
const { scanning } = storeToRefs(scanStore)

const loading = useLoading()
const { refresh, startScanAdd } = useDeviceActions()
const { isDark, toggleTheme } = useTheme()

async function handleLogout() {
  await authStore.logout(true)
  router.push('/login')
}

onMounted(async () => {
  if (!(await authStore.restore())) {
    router.push('/login')
  }
})
</script>

<style scoped>
.numbers-view { padding: 24px; max-width: 1440px; margin: 0 auto; }
</style>
