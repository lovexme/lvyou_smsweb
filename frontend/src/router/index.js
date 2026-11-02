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
  },
  {
    path: '/messages',
    name: 'Messages',
    component: () => import('../views/MessagesView.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/calls',
    name: 'Calls',
    component: () => import('../views/CallsView.vue'),
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

router.beforeEach(async (to, from, next) => {
  if (!_authStore) {
    const authed = JSON.parse(localStorage.getItem('auth') || '{}').authed
    return to.meta.requiresAuth && !authed ? next('/login') : next()
  }
  // A hard reload starts with an empty in-memory auth flag. Probe the
  // httpOnly cookie before bouncing the user back to the login page,
  // otherwise every refresh looks like a lost session.
  if (to.meta.requiresAuth && !_authStore.authed) {
    const ok = await _authStore.restore()
    if (!ok) return next('/login')
  }
  next()
})

export default router
