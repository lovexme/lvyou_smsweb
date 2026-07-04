import { ref, watch, onMounted } from 'vue'

const STORAGE_KEY = 'theme-preference'
const DARK_CLASS = 'dark-theme'

const isDark = ref(true)

function applyTheme(dark) {
  if (dark) {
    document.documentElement.classList.add(DARK_CLASS)
    document.documentElement.classList.remove('light-theme')
  } else {
    document.documentElement.classList.remove(DARK_CLASS)
    document.documentElement.classList.add('light-theme')
  }
  localStorage.setItem(STORAGE_KEY, dark ? 'dark' : 'light')
}

function loadTheme() {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved) {
    isDark.value = saved === 'dark'
  } else {
    isDark.value = window.matchMedia('(prefers-color-scheme: dark)').matches
  }
  applyTheme(isDark.value)
}

function toggleTheme() {
  isDark.value = !isDark.value
  applyTheme(isDark.value)
}

export function useTheme() {
  onMounted(() => {
    loadTheme()
  })

  return { isDark, toggleTheme, loadTheme }
}
