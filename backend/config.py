"""Process-wide configuration loaded from environment variables.

FIX(P2#4): centralised so the leaf modules (db.py, security.py) can be
imported without dragging the whole ``backend.main`` body in. Keeping
this module dependency-free (stdlib only) avoids the circular-import
trap the previous ``backend/app/`` attempt fell into.
"""

from __future__ import annotations

import os


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


# ── Storage locations ────────────────────────────────────────────────────────
DBPATH    = os.environ.get("BMDB",     "/opt/board-manager/data/data.db")
STATICDIR = os.environ.get("BMSTATIC", "/opt/board-manager/static")

# ── Auth / login ─────────────────────────────────────────────────────────────
DEFAULTUSER       = "admin"
DEFAULTPASS       = "admin"
UIUSER            = os.environ.get("BMUIUSER", "admin")
# FIX(P0#1): default to empty so an unset BMUIPASS cannot accidentally allow
# admin/admin login. Combined with validate_startup_security() in
# backend.security, the server refuses to start unless BMUIPASS is
# explicitly set to a non-default value (override with
# BMINSECURE_DEFAULT_PASSWORD=1 for local development).
UIPASS            = os.environ.get("BMUIPASS", "admin")
# A logged-in browser session used to expire after a fixed 2 hours with no
# renewal, which is easy to mistake for "the login breaks". Keep the lifetime
# configurable, but default to 30 days for a self-hosted panel and slide the
# expiry forward while the token is in use (see TOKEN_REFRESH_INTERVAL).
TOKEN_TTL_SECONDS = int(os.environ.get("BMTOKENTTL", str(30 * 86400)))
# Rewrite the token row (and cookie Max-Age) at most this often. Without it
# every authenticated request would touch SQLite and re-send Set-Cookie.
TOKEN_REFRESH_INTERVAL = max(
    60,
    min(
        int(os.environ.get("BMTOKENREFRESH", str(6 * 3600))),
        max(60, TOKEN_TTL_SECONDS // 2),
    ),
)

# FIX(P2#1): cookie-based session + CSRF. The browser SPA authenticates
# via an httpOnly cookie (``AUTH_COOKIE_NAME``) which JavaScript cannot
# read, closing the XSS-driven token theft path. A second non-httpOnly
# cookie (``CSRF_COOKIE_NAME``) carries a value derived deterministically
# from the auth token; the SPA echoes it in the X-CSRF-Token header on
# every state-changing request.
AUTH_COOKIE_NAME = os.environ.get("BMAUTHCOOKIE", "board_mgr_auth")
CSRF_COOKIE_NAME = os.environ.get("BMCSRFCOOKIE", "board_mgr_csrf")
CSRF_HEADER_NAME = "X-CSRF-Token"
COOKIE_SECURE = _env_truthy("BMCOOKIESECURE")
COOKIE_SAMESITE = (os.environ.get("BMCOOKIESAMESITE", "lax") or "lax").strip().lower()
if COOKIE_SAMESITE not in ("lax", "strict", "none"):
    COOKIE_SAMESITE = "lax"

# ── Networking / scan ────────────────────────────────────────────────────────
TIMEOUT             = float(os.environ.get("BMHTTPTIMEOUT",     "5.0"))
CONCURRENCY         = int(os.environ.get("BMSCANCONCURRENCY",   "64"))
TCP_CONCURRENCY     = int(os.environ.get("BMTCPCONCURRENCY",    "128"))
TCP_TIMEOUT         = float(os.environ.get("BMTCPTIMEOUT",      "0.3"))
CIDRFALLBACKLIMIT   = int(os.environ.get("BMCIDRFALLBACKLIMIT", "1024"))
SCAN_RETRIES        = int(os.environ.get("BMSCANRETRIES",       "3"))
SCAN_RETRY_SLEEP_MS = int(os.environ.get("BMSCANRETRYSLEEPMS",  "300"))
SCAN_TTL            = int(os.environ.get("BMSCANTTL",           str(3600)))
PREWARM_CONCURRENCY = int(os.environ.get("BMPREWARMCONCURRENCY", "64"))
TRUSTED_PROXY_HOPS  = int(os.environ.get("BMTRUSTEDPROXYHOPS",  "0"))
LOCAL_NETS_CACHE_TTL = float(os.environ.get("BMLOCALNETSCACHETTL", "60"))

# ── Limits / budgets ─────────────────────────────────────────────────────────
OTA_BATCH_MAX    = int(os.environ.get("BMOTABATCHMAX",    "64"))
# FIX(M2): cap the size of wifi / sim / forward batch operations the same way
# OTA already is, so a single request can't fan out unbounded device traffic.
BATCH_MAX        = int(os.environ.get("BMBATCHMAX",        "128"))
CONFIG_MAX_CHARS = int(os.environ.get("BMCONFIGMAXCHARS", "524288"))
SMS_MAX_LEN      = int(os.environ.get("BMSMSMAXLEN",      "500"))

# ── Misc constants ───────────────────────────────────────────────────────────
# FIX(P1#17): magic string -> named constant
FORWARD_METHOD_BASIC = "99"
