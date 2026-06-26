import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router, { setAuthStore } from './router'
import { useAuthStore } from './stores'
import './styles/app.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)

setAuthStore(useAuthStore())

app.mount('#app')
