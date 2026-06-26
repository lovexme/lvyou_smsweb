import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/dashboard'
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/LoginView.vue')
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('../views/DashboardView.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/numbers',
    name: 'Numbers',
    component: () => import('../views/NumbersView.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('../views/SettingsView.vue'),
    meta: { requiresAuth: true }
  }
]

const router = createRouter({
  history: createWebHistory('/static/'),
  routes
})

let _authStore = null

export function setAuthStore(store) {
  _authStore = store
}

router.beforeEach((to, from, next) => {
  const authed = _authStore
    ? _authStore.authed
    : JSON.parse(localStorage.getItem('auth') || '{}').authed
  if (to.meta.requiresAuth && !authed) {
    next('/login')
  } else {
    next()
  }
})

export default router
