import { ref } from 'vue'

import { dialDevice, fetchDeviceDetail, saveDeviceSim, setDeviceNote } from '../api/endpoints'
import { useDevicesStore } from '../stores'
import { useLoading } from './useLoading'
import { useNotice } from './useNotice'

// Device detail modal + inline SIM number edit/save. Singleton state shared by
// the device table row action and DetailModal.
const showDetailModal = ref(false)
const deviceDetail = ref(null)

// The number the operator calls to identify an unknown SIM. It is typed once
// and kept in the browser so the quick-dial buttons never ask again.
const MYPHONE_KEY = 'lvyou.myphone'

function readStoredPhone() {
  try {
    return localStorage.getItem(MYPHONE_KEY) || ''
  } catch (e) {
    return ''
  }
}

const myPhone = ref(readStoredPhone())

function setMyPhone(value) {
  myPhone.value = value
  try {
    if (value) localStorage.setItem(MYPHONE_KEY, value)
    else localStorage.removeItem(MYPHONE_KEY)
  } catch (e) {
    // Private-mode browsers can refuse storage; the field still works for
    // this session, which is better than failing the whole dialog.
  }
}

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

  // Dial the operator's own phone from one slot: the caller ID on the phone is
  // the SIM's number, which is the only reliable way to read it when the
  // carrier does not store MSISDN on the card. 20s cap so an unanswered call
  // cannot hold the line.
  async function quickDial(slot) {
    const device = deviceDetail.value && deviceDetail.value.device
    if (!device) return
    const phone = String(myPhone.value || '').trim()
    if (!/^\+?[0-9]{5,15}$/.test(phone)) {
      setNotice('请先填写你的手机号', 'err')
      return
    }
    loading.value = true
    try {
      const res = await dialDevice({
        deviceId: device.id,
        slot,
        phone,
        tts: '',
        duration: 20,
        tts_times: 0,
        after_action: 0
      })
      if (res && res.data && res.data.ok === false) {
        throw new Error(res.data.error || '拨号失败')
      }
      setNotice(`卡${slot} 已拨出，看你手机上的来电号码`, 'ok')
    } catch (e) {
      setNotice(errText(e, '拨号失败'), 'err')
    } finally {
      loading.value = false
    }
  }

  return {
    showDetailModal,
    deviceDetail,
    myPhone,
    setMyPhone,
    showDetail,
    closeDetailModal,
    saveSimSingle,
    updateDetailSim,
    updateDetailNote,
    saveNote,
    quickDial
  }
}
