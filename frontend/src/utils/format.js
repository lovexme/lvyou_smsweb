export function displayName(device) {
  return (device.alias || '').trim() || device.devId || device.ip
}

export function prettyTime(ts) {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

// History rows store the slot as "sim1"/"sim2"; show the board's own number
// next to it so two SIMs of one device are distinguishable at a glance.
export function simLabel(slot, number) {
  const label = '卡' + (String(slot || '').slice(-1) === '2' ? '2' : '1')
  const digits = String(number || '').replace(/[^0-9+]/g, '')
  return digits ? `${label} · ${digits}` : label
}
