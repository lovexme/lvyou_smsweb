import { onMounted, onBeforeUnmount } from 'vue'

export function useKeyboardShortcuts(shortcuts) {
  function handleKeydown(e) {
    const key = e.key.toLowerCase()
    const ctrl = e.ctrlKey || e.metaKey

    for (const [combo, handler] of Object.entries(shortcuts)) {
      const parts = combo.toLowerCase().split('+')
      const needCtrl = parts.includes('ctrl')
      const needKey = parts[parts.length - 1]

      if (needCtrl === ctrl && key === needKey) {
        e.preventDefault()
        handler()
        break
      }
    }
  }

  onMounted(() => window.addEventListener('keydown', handleKeydown))
  onBeforeUnmount(() => window.removeEventListener('keydown', handleKeydown))
}
