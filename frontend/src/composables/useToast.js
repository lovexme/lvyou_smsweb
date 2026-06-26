import { ref } from 'vue'

const toasts = ref([])
let id = 0

export function useToast() {
  function showToast(message, type = 'info', duration = 3000) {
    const toastId = ++id
    toasts.value.push({ id: toastId, message, type })

    if (duration > 0) {
      setTimeout(() => removeToast(toastId), duration)
    }

    return toastId
  }

  function removeToast(toastId) {
    const index = toasts.value.findIndex(t => t.id === toastId)
    if (index > -1) {
      toasts.value.splice(index, 1)
    }
  }

  return { toasts, showToast, removeToast }
}
