import { TOKEN_EXPIRES_KEY, TOKEN_KEY, api } from './client'

// FIX(P2#1): the auth token now lives in an httpOnly cookie set by the
// backend, so JavaScript no longer touches it. This module's job
// shrinks to (a) probing /api/me to find out if the cookie is valid,
// and (b) cleaning up stale storage from older builds (P1#10's
// sessionStorage and the original P0 localStorage).
const _LEGACY_KEYS = [TOKEN_KEY, TOKEN_EXPIRES_KEY]

function _purgeLegacyStorage() {
  try {
    for (const k of _LEGACY_KEYS) {
      window.sessionStorage.removeItem(k)
      window.localStorage.removeItem(k)
    }
  } catch {
    // sessionStorage / localStorage may be disabled in private browsing
    // contexts. Failing silently keeps the SPA usable on the login page.
  }
}

export async function checkAuth() {
  _purgeLegacyStorage()
  try {
    const resp = await api.get('/api/me')
    return {
      ok: true,
      username: (resp.data && resp.data.username) || '',
      expiresIn: (resp.data && resp.data.expiresIn) || 0
    }
  } catch {
    return { ok: false }
  }
}

// Kept as no-op shims so call sites do not need to learn a new shape.
// The cookie is set/cleared by the server on /api/login and /api/logout.
export function saveAuth() {
  _purgeLegacyStorage()
}

export function clearStoredAuth() {
  _purgeLegacyStorage()
}

export async function restoreAuth() {
  const r = await checkAuth()
  return r.ok
}
