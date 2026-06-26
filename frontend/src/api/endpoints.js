import { api } from './client'

export async function loginApi(password) {
  const response = await api.post('/api/login', { username: 'admin', password })
  return response.data || {}
}

export function logoutApi() {
  return api.post('/api/logout')
}

export function healthApi() {
  return api.get('/api/health')
}

// FIX(P1#7): /api/devices and /api/numbers now always return a paginated
// {items, total, page, page_size, pages} envelope. The Array.isArray
// branches keep the SPA forward-compatible with older bundled backends.
//
// FIX(P2#7): real server-side pagination + filter. Callers pass page,
// pageSize, q, group; the backend (PR #8) applies the same field
// matching the old client-side filter did and returns just one page.
export async function fetchDevicesPage({ page = 1, pageSize = 100, q = '', group = '' } = {}) {
  const params = { page, page_size: pageSize }
  if (q) params.q = q
  if (group && group !== 'all') params.group = group
  const resp = await api.get('/api/devices', { params })
  const data = resp.data
  if (Array.isArray(data)) {
    return { items: data, total: data.length, page: 1, pageSize: data.length, pages: 1 }
  }
  return {
    items: data.items || [],
    total: data.total || 0,
    onlineCount: data.online_count || 0,
    offlineCount: data.offline_count || 0,
    page: data.page || page,
    pageSize: data.page_size || pageSize,
    pages: data.pages || 0
  }
}

export async function fetchNumbersPage({ page = 1, pageSize = 100, q = '', group = '' } = {}) {
  const params = { page, page_size: pageSize }
  if (q) params.q = q
  // FIX(P2#7, Devin Review #8): pass through the group filter so the
  // dashboard SIM count stays consistent with the (group-filtered)
  // device counts. Empty / "all" means no filter.
  if (group && group !== 'all') params.group = group
  const resp = await api.get('/api/numbers', { params })
  const data = resp.data
  if (Array.isArray(data)) {
    return { items: data, total: data.length, page: 1, pageSize: data.length, pages: 1 }
  }
  return {
    items: data.items || [],
    total: data.total || 0,
    page: data.page || page,
    pageSize: data.page_size || pageSize,
    pages: data.pages || 0
  }
}

export async function fetchDeviceGroups() {
  // /api/devices/groups was added in P2#7 so the dropdown can stay
  // accurate even when the visible page only shows a subset of devices.
  // Fall back to an empty list if the backend is older.
  try {
    const resp = await api.get('/api/devices/groups')
    return (resp.data && resp.data.items) || []
  } catch {
    return []
  }
}

export function startScan(payload) {
  return api.post('/api/scan/start', payload)
}

export function getScanStatus(scanId) {
  return api.get('/api/scan/status/' + scanId)
}

export function sendSms(payload) {
  return api.post('/api/sms/send-direct', payload)
}

export function dialDevice(payload) {
  return api.post('/api/tel/dial', payload)
}

export function setDeviceAlias(deviceId, alias) {
  return api.post('/api/devices/' + deviceId + '/alias', { alias })
}

export function setDeviceGroup(deviceId, group) {
  return api.post('/api/devices/' + deviceId + '/group', { group })
}

export function deleteDeviceById(deviceId) {
  return api.delete('/api/devices/' + deviceId)
}

export function checkOtaBatch(deviceIds) {
  return api.post('/api/devices/batch/ota/check', { device_ids: deviceIds })
}

export function upgradeOtaBatch(deviceIds) {
  return api.post('/api/devices/batch/ota/upgrade', { device_ids: deviceIds })
}

export function readDeviceConfigs(deviceIds) {
  return api.post('/api/devices/batch/config/read', { device_ids: deviceIds })
}

export function previewDeviceConfig(payload) {
  return api.post('/api/devices/batch/config/preview', payload)
}

export function previewConfigPreset(deviceIds, preset) {
  return api.post('/api/devices/batch/config/preset/preview', { device_ids: deviceIds, preset })
}

export function writeDeviceConfig(payload) {
  return api.post('/api/devices/batch/config/write', payload)
}

export function writeConfigPreset(deviceIds, preset) {
  return api.post('/api/devices/batch/config/preset/write', { device_ids: deviceIds, preset })
}

export function previewWifiBatch(payload) {
  return api.post('/api/devices/batch/wifi/preview', payload)
}

export function applyWifiBatch(payload) {
  return api.post('/api/devices/batch/wifi', payload)
}

export function batchDeleteDevices(deviceIds) {
  return api.post('/api/devices/batch/delete', { device_ids: deviceIds })
}

export function fetchDeviceDetail(deviceId) {
  return api.get('/api/devices/' + deviceId + '/detail')
}

export function saveDeviceSim(deviceId, payload) {
  return api.post('/api/devices/' + deviceId + '/sim', payload)
}
