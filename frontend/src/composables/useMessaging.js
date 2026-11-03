import { ref } from 'vue'

import { fetchSmsJob, sendSms, dialDevice } from '../api/endpoints'
import { useLoading } from './useLoading'
import { useNotice } from './useNotice'

// SMS + dial workflow (they share the sender select + parse helper).
// Module-level singleton so the MessagePanel bindings and App.vue use the
// same state.
const commMode = ref('sms')
const fromSelected = ref('')
const toPhone = ref('')
const content = ref('')
const dialPhone = ref('')
const ttsText = ref('')

// "queued" only means the board accepted the command. The board reports the
// real result on a later webhook (402 -> 502 -> 503), so the UI keeps polling
// the job the backend opened instead of claiming the SMS went out.
const JOB_POLL_MS = 4000
const JOB_POLL_TRIES = 22
let pollToken = 0

const STATUS_TEXT = {
  queued: '板子已接收，等待发送结果…',
  sent: '板子已发出，等待运营商回执',
  delivered: '运营商确认送达',
}

function parseSenderValue() {
  const raw = String(fromSelected.value || '')
  const parts = raw.split('|')
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null
  const deviceId = Number(parts[0])
  const slot = Number(parts[1])
  if (!Number.isInteger(deviceId) || !Number.isInteger(slot)) return null
  return { deviceId, slot }
}

export function useMessaging() {
  const loading = useLoading()
  const { setNotice, errText } = useNotice()

  function watchJob(tid) {
    const mine = ++pollToken
    let tries = 0
    const tick = async () => {
      if (mine !== pollToken) return
      tries += 1
      try {
        const resp = await fetchSmsJob(tid)
        const data = (resp && resp.data) || {}
        if (mine !== pollToken) return
        if (data.status === 'failed') {
          setNotice('发送失败：' + (data.note || '板子未给出原因'), 'err')
          return
        }
        if (data.status === 'delivered' || data.status === 'sent') {
          setNotice(STATUS_TEXT[data.status] + (data.note && data.note !== STATUS_TEXT[data.status] ? ' · ' + data.note : ''), 'ok')
          if (data.status === 'delivered') return
        }
      } catch (e) {
        // 404 right after enqueue / transient network errors: keep waiting.
      }
      if (tries < JOB_POLL_TRIES) setTimeout(tick, JOB_POLL_MS)
    }
    setTimeout(tick, JOB_POLL_MS)
  }

  async function send() {
    if (!fromSelected.value || !toPhone.value || !content.value) {
      setNotice('请填写完整', 'err')
      return
    }
    const sender = parseSenderValue()
    if (!sender) {
      setNotice('请选择有效的发送卡号', 'err')
      return
    }
    loading.value = true
    try {
      const resp = await sendSms({
        deviceId: sender.deviceId,
        phone: toPhone.value,
        content: content.value,
        slot: sender.slot
      })
      // The backend answers 200 even when the board refuses the send, so the
      // ok flag is the real result; trusting the status code alone told users
      // "已发送" for messages the board had already dropped.
      if (resp && resp.data && resp.data.ok === false) {
        throw new Error(resp.data.error || '板子返回发送失败')
      }
      const tid = resp && resp.data && resp.data.tid
      setNotice('板子已接收，等待发送结果…', 'info')
      if (tid) watchJob(tid)
      toPhone.value = ''
      content.value = ''
    } catch (e) {
      setNotice(errText(e, '发送失败'), 'err')
    } finally {
      loading.value = false
    }
  }

  async function dial() {
    if (!fromSelected.value || !dialPhone.value) {
      setNotice('请填写完整', 'err')
      return
    }
    const sender = parseSenderValue()
    if (!sender) {
      setNotice('请选择有效的拨号卡号', 'err')
      return
    }
    loading.value = true
    try {
      await dialDevice({
        deviceId: sender.deviceId,
        phone: dialPhone.value,
        slot: sender.slot,
        tts: ttsText.value
      })
      setNotice('已拨出', 'ok')
      dialPhone.value = ''
      ttsText.value = ''
    } catch (e) {
      setNotice(errText(e, '拨号失败'), 'err')
    } finally {
      loading.value = false
    }
  }

  return { commMode, fromSelected, toPhone, content, dialPhone, ttsText, send, dial }
}
