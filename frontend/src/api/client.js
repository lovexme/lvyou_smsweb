import axios from 'axios'

// FIX(P2#1): withCredentials lets the browser attach our httpOnly auth
// cookie on cross-origin requests when BMALLOWORIGINS is configured.
// Same-origin (localhost-served-SPA) calls work either way, but having
// the flag set unconditionally keeps the dev/prod CORS shape identical.
export const api = axios.create({ baseURL: '', withCredentials: true })

// FIX(P2#1): kept exported for backwards-compat with any code that still
// imports these names. The cookie migration drops sessionStorage as the
// auth boundary -- the constants are now only used to clean up legacy
// keys left over from older builds.
export const TOKEN_KEY = 'board_mgr_token'
export const TOKEN_EXPIRES_KEY = 'board_mgr_token_expires'
export const CSRF_COOKIE_NAME = 'board_mgr_csrf'
export const CSRF_HEADER_NAME = 'X-CSRF-Token'

export function setToken(_token) {
  // No-op after the cookie migration. The browser handles Set-Cookie on
  // /api/login and we never see the auth token in JS again.
}

export function clearToken() {
  // No-op after the cookie migration. Logout clears cookies server-side.
}

function _readCookie(name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const m = document.cookie.match(new RegExp('(?:^|; )' + escaped + '=([^;]*)'))
  return m ? decodeURIComponent(m[1]) : ''
}

const _CSRF_SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

api.interceptors.request.use((config) => {
  const method = (config.method || 'get').toUpperCase()
  if (_CSRF_SAFE_METHODS.has(method)) return config
  const csrf = _readCookie(CSRF_COOKIE_NAME)
  if (csrf) {
    config.headers = config.headers || {}
    config.headers[CSRF_HEADER_NAME] = csrf
  }
  return config
})
