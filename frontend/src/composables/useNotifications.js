/**
 * 推送通知管理
 * 使用 Web Notification API（兼容所有环境）
 * 在 Capacitor 环境中可扩展为原生推送
 */

import { ref } from 'vue'

const pushToken = ref(null)
const notificationPermission = ref('default')

/**
 * 初始化推送通知
 */
export async function initPushNotifications() {
  if (!('Notification' in window)) {
    notificationPermission.value = 'unsupported'
    return
  }

  if (Notification.permission === 'granted') {
    notificationPermission.value = 'granted'
  } else if (Notification.permission !== 'denied') {
    try {
      const perm = await Notification.requestPermission()
      notificationPermission.value = perm
    } catch (_) {
      notificationPermission.value = 'denied'
    }
  } else {
    notificationPermission.value = 'denied'
  }
}

/**
 * 发送本地通知
 */
export function sendNotification(title, body, options = {}) {
  if (notificationPermission.value !== 'granted') return

  try {
    new Notification(title, {
      body,
      icon: '/static/pwa-192x192.png',
      badge: '/static/pwa-192x192.png',
      vibrate: [200, 100, 200],
      ...options
    })
  } catch (_) {
    // 静默失败
  }
}

/**
 * 设备状态变化通知
 */
export function notifyDeviceStatus(deviceName, status) {
  const emoji = status === 'online' ? '🟢' : '🔴'
  sendNotification(
    `${emoji} 设备${status === 'online' ? '上线' : '离线'}`,
    `${deviceName} 已${status === 'online' ? '上线' : '离线'}`
  )
}

/**
 * 新短信通知
 */
export function notifyNewSms(from, content) {
  sendNotification(
    '📩 收到新短信',
    `来自 ${from}：${content.substring(0, 50)}${content.length > 50 ? '...' : ''}`
  )
}

/**
 * OTA 完成通知
 */
export function notifyOtaComplete(deviceName, success) {
  sendNotification(
    success ? '✅ OTA 升级成功' : '❌ OTA 升级失败',
    `${deviceName} ${success ? '已成功升级' : '升级失败，请检查'}`
  )
}

export { pushToken, notificationPermission }
