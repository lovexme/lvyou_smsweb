import { ref } from 'vue'

import { autoDetectSim, fetchDeviceDetail, saveDeviceSim, setDeviceNote } from '../api/endpoints'
import { useDevicesStore } from '../stores'
import { useLoading } from './useLoading'
import { useNotice } from './useNotice'

// Device detail modal + inline SIM number edit/save. Singleton state shared by
// the device table row action and DetailModal.
const showDetailModal = ref(false)
const deviceDetail = ref(null)

export function useDetail() {
  const loading = useLoading()
  const { setNotice, errText } = useNotice()
  const devicesStore = useDevicesStore()

  async function showDetail(device) {
    loading.value = true
    try {
      const response = await fetchDeviceDetail(device.id)
      deviceDetail.value = response.data
      showDetailModal.value = true
    } catch (e) {
      setNotice(errText(e, '获取详情失败'), 'err')
    } finally {
      loading.value = false
    }
  }

  function closeDetailModal() {
    showDetailModal.value = false
    deviceDetail.value = null
  }

  async function saveSimSingle() {
    const id = deviceDetail.value && deviceDetail.value.device && deviceDetail.value.device.id
    if (!id) return
    loading.value = true
    try {
      const res = await saveDeviceSim(id, {
        sim1: deviceDetail.value.device.sim1number || '',
        sim2: deviceDetail.value.device.sim2number || ''
      })
      const board = res.data && res.data.board
      setNotice(board === 'updated' ? '已保存卡号' : '已保存到面板（板子暂时没同步）', 'ok')
      await devicesStore.refresh()
    } catch (e) {
      setNotice(errText(e, '保存失败'), 'err')
    } finally {
      loading.value = false
    }
  }

  function updateDetailSim(field, value) {
    if (deviceDetail.value && deviceDetail.value.device) {
      deviceDetail.value.device[field] = value
    }
  }

  function updateDetailNote(value) {
    if (deviceDetail.value && deviceDetail.value.device) {
      deviceDetail.value.device.note = value
    }
  }

  async function saveNote() {
    const device = deviceDetail.value && deviceDetail.value.device
    if (!device) return
    loading.value = true
    try {
      await setDeviceNote(device.id, device.note || '')
      setNotice('已保存备注', 'ok')
      await devicesStore.refresh()
    } catch (e) {
      setNotice(errText(e, '保存备注失败'), 'err')
    } finally {
      loading.value = false
    }
  }

  async function detectSim() {
    const device = deviceDetail.value && deviceDetail.value.device
    if (!device) return
    loading.value = true
    try {
      const res = await autoDetectSim(device.id)
      const data = (res && res.data) || {}
      if (data.found) {
        device.sim1number = data.sim1 || device.sim1number || ''
        device.sim2number = data.sim2 || device.sim2number || ''
        setNotice('已从板子读取到卡号', 'ok')
      } else {
        setNotice('板子读不到卡号（运营商未在卡里预存号码），请手动填写', 'warn')
      }
    } catch (e) {
      setNotice(errText(e, '自动识别失败'), 'err')
    } finally {
      loading.value = false
    }
  }

  return {
    showDetailModal,
    deviceDetail,
    showDetail,
    closeDetailModal,
    saveSimSingle,
    updateDetailSim,
    updateDetailNote,
    saveNote,
    detectSim
  }
}
