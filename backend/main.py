import asyncio
import json
import os
import re
import threading
import time
from datetime import datetime
from ipaddress import ip_address, ip_network, IPv4Network
from itertools import islice
from typing import Any, Dict, List, Optional, Tuple
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Depends, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

import concurrent.futures
import hashlib
import hmac
import logging
import uuid as _uuid
from collections import defaultdict
from logging.handlers import RotatingFileHandler
from threading import Lock as _Lock

# FIX(P2#4): leaf modules (config / db / security) own the SQLAlchemy
# engine, models, token CRUD, SSRF allowlist and network helpers. Routes
# and middleware stay in this file for now; subsequent PRs can split
# them into devices/scan/ota/config_io APIRouters.
from backend.config import (
    AUTH_COOKIE_NAME,
    CIDRFALLBACKLIMIT,
    CONCURRENCY,
    CONFIG_MAX_CHARS,
    DBPATH,
    COOKIE_SAMESITE,
    COOKIE_SECURE,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    DEFAULTPASS,
    DEFAULTUSER,
    FORWARD_METHOD_BASIC,
    OTA_BATCH_MAX,
    SCAN_RETRIES,
    SCAN_RETRY_SLEEP_MS,
    SCAN_TTL,
    SMS_MAX_LEN,
    STATICDIR,
    TIMEOUT,
    TOKEN_TTL_SECONDS,
    UIPASS,
    UIUSER,
)
from backend.db import (
    Device,
    SessionLocal,
    cleanup_expired_tokens as _cleanup_expired_tokens,
    delete_token as _delete_token,
    get_db,
    get_token_record as _get_token_record,
    issue_token as _issue_token,
    nowts,
)
from backend.security import (
    client_ip_from_request as _client_ip,
    get_arp_table as getarptable,
    guess_ipv4_cidr as guessipv4cidr,
    is_device_ip_allowed as _is_device_ip_allowed,
    prewarm_neighbors,
    tcp_port_open as _tcp_port_open,
    validate_startup_security as _validate_startup_security,
    set_shared_executor as _set_shared_executor,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("board-manager")


def _env_truthy(name: str) -> bool:
    """FIX(P2#12): forward-declared so the BMDEBUG check below treats
    BMDEBUG=0 as false. Previously the bare ``os.environ.get("BMDEBUG")``
    truthiness test enabled debug logging when an operator wrote
    ``BMDEBUG=0`` (a non-empty string is truthy in Python). The same
    helper is reused later for BMINSECURE_DEFAULT_PASSWORD and friends."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


logger.setLevel(logging.DEBUG if _env_truthy("BMDEBUG") else logging.INFO)

# Constants, engine, and ORM models now live in backend.config / backend.db
# and are re-imported above. The names are unchanged so the rest of this
# module reads identically.


class RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self._max    = max_calls
        self._period = period
        self._hits: Dict[str, list] = defaultdict(list)
        self._lock   = _Lock()

    def _prune_locked(self, key: str, now: float) -> list:
        window = [t for t in self._hits.get(key, []) if now - t < self._period]
        self._hits[key] = window
        return window

    def allow(self, key: str) -> bool:
        """Check the limit and record this attempt atomically."""
        now = time.time()
        with self._lock:
            window = self._prune_locked(key, now)
            if len(window) >= self._max:
                return False
            window.append(now)
            return True

    def check_only(self, key: str) -> bool:
        """FIX(P2#2): non-recording probe. Used by the login flow to gate
        before validating credentials so a flood of well-formed login
        requests does not consume the budget for legitimate users."""
        now = time.time()
        with self._lock:
            return len(self._prune_locked(key, now)) < self._max

    def record(self, key: str) -> None:
        """FIX(P2#2): record an event without a guard check. Paired with
        check_only() the caller can implement count-failures-only
        semantics: fat-fingering a password and then succeeding does not
        eat the budget, but failed attempts do."""
        now = time.time()
        with self._lock:
            window = self._prune_locked(key, now)
            window.append(now)

    def reset(self, key: str) -> None:
        """FIX(P2#2): clear the failure window after a successful login."""
        with self._lock:
            self._hits.pop(key, None)

    def remaining(self, key: str) -> int:
        now = time.time()
        with self._lock:
            window = [t for t in self._hits.get(key, []) if now - t < self._period]
            return max(0, self._max - len(window))


_sms_limiter  = RateLimiter(int(os.environ.get("BMSMSRATELIMIT",  "10")), float(os.environ.get("BMSMSRATEPERIOD",  "60")))
_dial_limiter = RateLimiter(int(os.environ.get("BMDIALRATELIMIT",  "5")), float(os.environ.get("BMDIALRATEPERIOD", "60")))
# FIX(P2#2): two-dimensional login rate limit. The IP limiter catches one
# attacker probing many usernames; the username limiter catches a
# distributed bruteforce of a single known account from many addresses.
# Both are count-failures-only so legitimate users with one or two typos
# are not locked out alongside attackers. Defaults stay at 5 failures /
# 60s for IP (preserves the previous BMLOGINRATELIMIT/PERIOD knobs) and
# 10 / 600 for username (looser but with a much wider window so a slow
# distributed attack still hits the cap before exhausting the password
# space).
_login_limiter_ip = RateLimiter(
    int(os.environ.get("BMLOGINRATELIMIT", "5")),
    float(os.environ.get("BMLOGINRATEPERIOD", "60")),
)
_login_limiter_user = RateLimiter(
    int(os.environ.get("BMLOGINUSERRATELIMIT", "10")),
    float(os.environ.get("BMLOGINUSERRATEPERIOD", "600")),
)
# FIX(N5): OTA batch rate limiter (per user), prevents using it as an internal reboot-storm
_ota_limiter  = RateLimiter(int(os.environ.get("BMOTARATELIMIT",  "4")), float(os.environ.get("BMOTARATEPERIOD",  "60")))

PHONE_RE = re.compile(r"^\+?[0-9]{5,15}$")


def _validate_phone(phone: str) -> str:
    p = (phone or "").strip()
    if not p or not PHONE_RE.match(p):
        raise HTTPException(status_code=400, detail="手机号格式不正确")
    return p


def _validate_sms_content(content: str) -> str:
    c = (content or "").strip()
    if not c:
        raise HTTPException(status_code=400, detail="短信内容不能为空")
    if len(c) > SMS_MAX_LEN:
        raise HTTPException(status_code=400, detail=f"短信内容超出长度限制（最多{SMS_MAX_LEN}字）")
    return c


# FIX(P2#3): file-backed structured audit log with size-based rotation.
# Defaults colocate audit.log next to the SQLite DB so the systemd unit's
# existing ReadWritePaths covers it without further changes; operators
# can redirect via BMAUDITLOGFILE (e.g. to /var/log/board-manager/) or
# disable file logging entirely with BMAUDITLOGDISABLE=1 (e.g. inside a
# container where stdout shipping is preferred).
_audit_default_dir = os.path.dirname(DBPATH) or "/var/log/board-manager"
AUDIT_LOG_FILE         = os.environ.get("BMAUDITLOGFILE", os.path.join(_audit_default_dir, "audit.log"))
AUDIT_LOG_MAX_BYTES    = int(os.environ.get("BMAUDITLOGMAXBYTES",    str(10 * 1024 * 1024)))
AUDIT_LOG_BACKUP_COUNT = int(os.environ.get("BMAUDITLOGBACKUPCOUNT", "5"))
AUDIT_LOG_DISABLE      = os.environ.get("BMAUDITLOGDISABLE", "").strip().lower() in {"1", "true", "yes", "on"}


class _JsonAuditFormatter(logging.Formatter):
    """FIX(P2#3): emit one JSON object per line so log shippers (Loki,
    ELK, Datadog, journald JSON parser) can index fields without the
    fragile `key=value` regex parsing the legacy text format required."""

    def format(self, record):
        ts = datetime.utcfromtimestamp(record.created).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        payload = {
            "ts":     ts,
            "level":  record.levelname,
            "action": getattr(record, "audit_action", record.getMessage()),
            "user":   getattr(record, "audit_user", "-"),
            "detail": getattr(record, "audit_detail", ""),
        }
        ip = getattr(record, "audit_ip", "")
        if ip:
            payload["ip"] = ip
        result = getattr(record, "audit_result", "")
        if result:
            payload["result"] = result
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _setup_audit_logger() -> logging.Logger:
    """FIX(P2#3): configure the audit logger with two sinks: (a) a stream
    handler so journalctl still shows recent activity, and (b) a rotating
    file handler with the JSON formatter so the audit trail survives
    journal rotation and can be shipped externally. The function is
    idempotent so repeated imports (eg. pytest collection) don't pile up
    duplicate handlers."""

    aud = logging.getLogger("audit")
    aud.setLevel(logging.INFO)
    aud.propagate = False  # don't double-emit through the root logger
    if aud.handlers:
        return aud

    stream = logging.StreamHandler()
    # FIX(P2#3): include ip + result so the journal-readable form does
    # not drop the structured fields the JSON formatter persists.
    stream.setFormatter(logging.Formatter(
        "audit ts=%(asctime)s action=%(audit_action)s user=%(audit_user)s "
        "ip=%(audit_ip)s result=%(audit_result)s detail=%(audit_detail)s"
    ))
    aud.addHandler(stream)

    if AUDIT_LOG_DISABLE:
        return aud

    try:
        log_dir = os.path.dirname(AUDIT_LOG_FILE)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        fh = RotatingFileHandler(
            AUDIT_LOG_FILE,
            maxBytes=AUDIT_LOG_MAX_BYTES,
            backupCount=AUDIT_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setFormatter(_JsonAuditFormatter())
        aud.addHandler(fh)
    except OSError as exc:
        # Don't fail startup just because the audit dir isn't writable;
        # the stream handler keeps records visible and the warning lands
        # in the operator's main service log.
        logger.warning("audit file logger disabled (%s): %s", AUDIT_LOG_FILE, exc)
    return aud


_audit_logger = _setup_audit_logger()


def _audit(action: str, user: str = "-", detail: str = "", ip: str = "", result: str = ""):
    """FIX(P2#3): all audit call sites now flow through structured fields
    (`extra=...`) instead of being baked into the message string. The
    JSON formatter consumes those fields directly; the legacy stream
    formatter renders them as the old `key=value` shape so existing
    journal greps keep working during the transition."""
    _audit_logger.info(
        action,
        extra={
            "audit_action": action,
            "audit_user":   user,
            "audit_detail": detail,
            "audit_ip":     ip,
            "audit_result": result,
        },
    )


# FIX(P1#16): only swallow truly unhandled Exceptions. HTTPExceptions
# originate from validation / auth code paths and should keep their intended
# status codes; Starlette's default handler already handles them correctly.
def _setup_exception_handlers(_app: FastAPI):
    @_app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        if isinstance(exc, HTTPException):
            raise exc
        err_id = _uuid.uuid4().hex[:8]
        logger.error("unhandled [%s] %s %s: %s", err_id, request.method, request.url.path, exc, exc_info=True)
        return JSONResponse(status_code=500, content={"detail": f"服务器内部错误 (ref: {err_id})"})


class ScanState:
    def __init__(self):
        self.status    = "pending"
        self.progress  = ""
        self.results: List[Dict[str, Any]] = []
        self.found     = 0
        self.scanned   = 0
        self.total_ips = 0
        self.cidr      = ""
        self.finished_at: float = 0.0
        self._lock     = _Lock()

    # FIX(P1#7): all mutators serialised under the same lock used by to_dict
    def set_status(self, status: str, progress: Optional[str] = None) -> None:
        with self._lock:
            self.status = status
            if progress is not None:
                self.progress = progress

    def set_progress(self, progress: str) -> None:
        with self._lock:
            self.progress = progress

    def set_counts(self, *, scanned: Optional[int] = None, found: Optional[int] = None, total_ips: Optional[int] = None) -> None:
        with self._lock:
            if scanned is not None:
                self.scanned = scanned
            if found is not None:
                self.found = found
            if total_ips is not None:
                self.total_ips = total_ips

    def set_results(self, results: List[Dict[str, Any]]) -> None:
        with self._lock:
            self.results = results
            self.found = len(results)

    def set_cidr(self, cidr: str) -> None:
        with self._lock:
            self.cidr = cidr

    def mark_done(self) -> None:
        with self._lock:
            self.finished_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "status":    self.status,
                "progress":  self.progress,
                "found":     self.found,
                "scanned":   self.scanned,
                "total_ips": self.total_ips,
                "cidr":      self.cidr,
                "devices":   [{"ip": r["ip"], "devId": r.get("devId", "")} for r in self.results],
            }


_active_scans: Dict[str, ScanState] = {}
_active_scans_lock = _Lock()


def _cleanup_old_scans() -> None:
    now = time.time()
    with _active_scans_lock:
        expired = [sid for sid, st in _active_scans.items()
                   if st.finished_at > 0 and now - st.finished_at > SCAN_TTL]
        for sid in expired:
            _active_scans.pop(sid, None)


# ``_run_migrations`` runs at import time inside backend.db; ``get_db``,
# ``nowts``, the token CRUD and ``_issue_token`` are imported above and
# remain available under their original ``_-prefixed`` names for the
# rest of this file.


def _extract_bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "").strip()
    if not auth.startswith("Bearer "):
        return ""
    return auth[7:].strip()


def _extract_request_token(request: Request) -> Tuple[str, bool]:
    """FIX(P2#1): return (token, via_cookie). Authorization header takes
    precedence (CLI / programmatic clients keep working), falling back to
    the AUTH_COOKIE_NAME cookie for browser sessions. The boolean tells
    the caller whether the token came from a cookie -- only cookie auth
    is vulnerable to CSRF and therefore requires the X-CSRF-Token check."""
    bearer = _extract_bearer_token(request)
    if bearer:
        return bearer, False
    cookie_token = request.cookies.get(AUTH_COOKIE_NAME, "")
    return cookie_token.strip(), True


def _csrf_for_token(token: str) -> str:
    """FIX(P2#1): deterministic CSRF derivation. We do not persist a
    separate CSRF column; rotating BMUIPASS rotates the HMAC key and
    therefore invalidates every previously-issued CSRF cookie."""
    if not token:
        return ""
    key = hashlib.sha256(b"board-mgr-csrf-v1::" + UIPASS.encode("utf-8")).digest()
    return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


def _set_auth_cookies(response, token: str) -> None:
    """FIX(P2#1): write both auth (httpOnly) and CSRF (JS-readable) cookies."""
    csrf = _csrf_for_token(token)
    response.set_cookie(
        AUTH_COOKIE_NAME, token,
        max_age=TOKEN_TTL_SECONDS,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME, csrf,
        max_age=TOKEN_TTL_SECONDS,
        httponly=False,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
    )


def _clear_auth_cookies(response) -> None:
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


def _unauthorized_json(detail: str = "未登录或登录已失效") -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": detail})


def _forbidden_json(detail: str) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": detail})


def _require_token(request: Request) -> Dict[str, Any]:
    token, _ = _extract_request_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="未登录或登录已失效")
    payload = _get_token_record(token)
    if not payload:
        raise HTTPException(status_code=401, detail="未登录或登录已失效")
    if payload.get("exp", 0) <= nowts():
        _delete_token(token)
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return payload


def _check_login_credentials(username: str, password: str) -> bool:
    return hmac.compare_digest(username, UIUSER) and hmac.compare_digest(password, UIPASS)


# `_validate_startup_security` and `_client_ip` are imported from
# ``backend.security`` and remain available under their original names.
# The cookie SameSite/Secure consistency check that lived here previously
# now also lives in ``backend.security.validate_startup_security``.


# ── Shared httpx client + executor (managed by lifespan) ─────────────────────
_sync_client: Optional[httpx.Client] = None
_shared_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
_cleanup_task: Optional[asyncio.Task] = None


def _get_sync_client() -> httpx.Client:
    """Return the shared sync client; fall back to creating a fresh one if the
    lifespan manager has not run yet (e.g. during tests)."""
    global _sync_client
    if _sync_client is None:
        _sync_client = httpx.Client(
            timeout=TIMEOUT,
            limits=httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=20),
            follow_redirects=False,
        )
    return _sync_client


def _get_shared_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _shared_executor
    if _shared_executor is None:
        _shared_executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(CONCURRENCY, 32))
    return _shared_executor


async def _scan_cleanup_loop() -> None:
    while True:
        try:
            _cleanup_old_scans()
            _cleanup_expired_tokens()
        except Exception:
            logger.debug("background cleanup error", exc_info=True)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _sync_client, _shared_executor, _cleanup_task
    # FIX(P0#1): refuse to start with an unset / default BMUIPASS.
    _validate_startup_security()
    # FIX(P1#9): one sync client for the whole process, connection pooled.
    _sync_client = httpx.Client(
        timeout=TIMEOUT,
        limits=httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=20),
        follow_redirects=False,
    )
    # FIX(P1#10): one ThreadPoolExecutor for all batch endpoints / scans.
    _shared_executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(CONCURRENCY, 32))
    # Share the executor with security.prewarm_neighbors
    _set_shared_executor(_shared_executor)
    # FIX(P1#6): the previous AsyncClient on app.state was created but never
    # used by any endpoint. Removed to avoid leaking connections at shutdown
    # and to make it obvious which client is the production code path.
    app.state.sync_http_client = _sync_client
    app.state.executor = _shared_executor
    # FIX(P1#12): periodic cleanup task for finished scan tasks and expired tokens.
    _cleanup_task = asyncio.create_task(_scan_cleanup_loop())
    try:
        yield
    finally:
        if _cleanup_task:
            _cleanup_task.cancel()
            try:
                await _cleanup_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            _sync_client.close()
        except Exception:
            pass
        try:
            _shared_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass


app = FastAPI(title="Board LAN Hub", version="5.0", lifespan=lifespan)
_setup_exception_handlers(app)


def _configure_cors(_app: FastAPI) -> None:
    raw = os.environ.get("BMALLOWORIGINS", "")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    # FIX(P0#6): refuse the insecure combination of wildcard origin with
    # credentials. Starlette silently allows it but browsers will reject the
    # response, which bakes a subtle CSRF-enabling footgun into the API.
    if "*" in origins:
        raise RuntimeError(
            "BMALLOWORIGINS='*' is incompatible with allow_credentials=True. "
            "Either specify explicit origins or unset BMALLOWORIGINS."
        )
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        # FIX(P2#1): allow X-CSRF-Token so the SPA can attach the double-
        # submit value on cross-origin state-changing requests.
        allow_headers=["Authorization", "Content-Type", CSRF_HEADER_NAME],
    )


# FIX(P0#1): expose /api/health so container/compose HEALTHCHECK works without
# needing a Bearer token. The endpoint returns only liveness info.
# FIX(P2#8): /metrics is added to this set inside _wire_prometheus()
# only when BMMETRICS_TOKEN is set. Leaving it out when metrics are
# disabled means the main token_auth_mw still rejects requests instead
# of letting them fall through to a confusing 404.
_PUBLIC_PATHS = {"/", "/api/login", "/api/health"}

# FIX(P2#1): methods that mutate state and therefore must carry a valid
# X-CSRF-Token header when the caller authenticated via a cookie. Bearer-
# header authentication does not need CSRF since CSRF is specifically
# about cookies-attached-by-the-browser.
_CSRF_REQUIRED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def token_auth_mw(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path.startswith("/static/") or path in _PUBLIC_PATHS:
        return await call_next(request)
    token, via_cookie = _extract_request_token(request)
    if not token:
        return _unauthorized_json("未登录或登录已失效")
    record = _get_token_record(token)
    if not record:
        return _unauthorized_json("未登录或登录已失效")
    if record.get("exp", 0) <= nowts():
        _delete_token(token)
        return _unauthorized_json("登录已过期，请重新登录")
    # FIX(P2#1): CSRF gate. Only enforced on cookie-auth + state-changing
    # methods so that bearer-token CLIs and read-only GETs are unaffected.
    if via_cookie and request.method in _CSRF_REQUIRED_METHODS:
        provided = request.headers.get(CSRF_HEADER_NAME, "").strip()
        expected = _csrf_for_token(token)
        if not provided or not hmac.compare_digest(provided, expected):
            return _forbidden_json("CSRF token 缺失或不匹配")
    return await call_next(request)


# FIX(P1#5): register CORS *after* the token middleware so it is the
# outermost wrapper. Starlette runs middlewares in last-added-first order,
# so by adding CORS after the @app.middleware("http") decorator above we
# guarantee that 401 responses returned directly from the auth middleware
# still carry Access-Control-Allow-* headers (otherwise the browser hides
# them as a generic network error and the SPA cannot prompt for re-login).
_configure_cors(app)


# FIX(P2#8): Prometheus instrumentation. The default instrumentator
# emits per-request HTTP histogram + counter under
# `http_request_duration_seconds` / `http_requests_total`. We add three
# custom gauges that are cheap to keep up to date:
#   - bm_devices_total   (refreshed lazily inside /metrics)
#   - bm_devices_online  (same)
#   - bm_login_failures  (incremented in the login endpoint via
#                         _bm_login_failures.labels(reason=...).inc())
# /metrics is still routed through the auth middleware bypass list, but
# we additionally require a bearer token (BMMETRICS_TOKEN). If the env
# var is not set, /metrics is *not registered at all* -- safer default
# than "everyone on the LAN can scrape device counts".
def _wire_prometheus(_app: FastAPI) -> None:
    metrics_token = os.environ.get("BMMETRICS_TOKEN", "").strip()
    if not metrics_token:
        return  # opt-in only; absent env var = no /metrics endpoint
    _PUBLIC_PATHS.add("/metrics")

    try:
        from prometheus_client import Counter, Gauge
        from prometheus_fastapi_instrumentator import Instrumentator
    except ImportError:
        # The dependency is in requirements.txt but in the unlikely case
        # an operator strips it from a custom build, fail loudly during
        # setup rather than mysteriously at scrape time.
        raise RuntimeError(
            "BMMETRICS_TOKEN is set but prometheus_fastapi_instrumentator "
            "is not installed. Install backend/requirements.txt or unset "
            "BMMETRICS_TOKEN."
        )

    _app.state.bm_devices_total = Gauge("bm_devices_total", "Total devices currently registered")
    _app.state.bm_devices_online = Gauge("bm_devices_online", "Devices currently marked online")
    _app.state.bm_login_failures = Counter(
        "bm_login_failures_total",
        "Failed login attempts grouped by reason",
        ["reason"],
    )

    instrumentator = Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/api/health"],
    )
    instrumentator.instrument(_app)

    # Refresh the device gauges on every scrape via a tiny middleware on
    # the metrics route itself. Cheap (two .count() queries) and avoids
    # having to hook every place that mutates a device.
    def _refresh_device_gauges() -> None:
        try:
            with SessionLocal() as session:
                total = session.query(Device).count()
                online = session.query(Device).filter(Device.status == "online").count()
            _app.state.bm_devices_total.set(total)
            _app.state.bm_devices_online.set(online)
        except Exception:
            # Don't let a slow / locked DB block scraping.
            pass

    @_app.middleware("http")
    async def _metrics_auth_mw(request: Request, call_next):
        if request.url.path == "/metrics":
            auth = request.headers.get("authorization", "")
            if not auth.startswith("Bearer "):
                return _unauthorized_json("metrics token required")
            supplied = auth[len("Bearer "):].strip()
            import hmac as _hmac
            if not _hmac.compare_digest(supplied, metrics_token):
                return _unauthorized_json("metrics token invalid")
            _refresh_device_gauges()
        return await call_next(request)

    # Expose at /metrics. Must be called after instrument() above.
    instrumentator.expose(_app, endpoint="/metrics", include_in_schema=False)


_wire_prometheus(app)


def _bm_login_failure(reason: str) -> None:
    """Increment the login-failure counter when /metrics is enabled.
    No-op when BMMETRICS_TOKEN is unset (the counter wasn't created)."""
    counter = getattr(app.state, "bm_login_failures", None)
    if counter is not None:
        try:
            counter.labels(reason=reason).inc()
        except Exception:
            pass


os.makedirs(STATICDIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATICDIR), name="static")


@app.get("/")
def uiindex():
    index_path = os.path.join(STATICDIR, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="UI not built")
    return FileResponse(index_path)


# Network-level helpers (SSRF allowlist, ARP table, prewarm, TCP probe,
# default CIDR guess) live in ``backend.security``; only the thin
# HTTP-aware wrapper that converts allowlist failures into HTTPException
# stays here so it can use FastAPI's exception machinery.
def _ensure_device_ip_allowed(ip: str) -> None:
    if not _is_device_ip_allowed(ip):
        logger.warning("blocked outbound device request to non-whitelisted ip: %s", ip)
        raise HTTPException(status_code=400, detail="设备 IP 不在允许的内网范围内")


def _bm_op_from_sta(sta: str) -> str:
    return (sta or "").strip()


# FIX(P1#9): reuse shared httpx.Client instead of creating one per call.
def istargetdevice(ip: str, user: str, pw: str) -> Tuple[bool, Optional[str]]:
    _ensure_device_ip_allowed(ip)
    url = f"http://{ip}/mgr"
    last_realm: Optional[str] = None
    client = _get_sync_client()
    for attempt in range(max(1, SCAN_RETRIES)):
        try:
            resp = client.get(url)
            if resp.status_code != 401:
                raise RuntimeError(f"unexpected status {resp.status_code}")
            header = resp.headers.get("www-authenticate", "")
            if "Digest" not in header:
                raise RuntimeError("digest auth missing")
            match = re.search(r'realm="([^"]+)"', header)
            realm = match.group(1) if match else None
            last_realm = realm
            if realm != "asyncesp":
                return False, realm
            resp2 = client.get(url, auth=httpx.DigestAuth(user, pw))
            if resp2.status_code == 200:
                return True, realm
            raise RuntimeError(f"auth status {resp2.status_code}")
        except Exception as _scan_exc:
            if attempt < max(1, SCAN_RETRIES) - 1:
                logger.debug("scan %s attempt %d failed: %s", ip, attempt + 1, _scan_exc)
                time.sleep(max(0, SCAN_RETRY_SLEEP_MS) / 1000.0)
    return False, last_realm


def getdevicedata(ip: str, user: str, pw: str) -> Optional[Dict[str, Any]]:
    _ensure_device_ip_allowed(ip)
    keys_list = ["DEV_ID", "DEV_VER", "SIM1_PHNUM", "SIM2_PHNUM", "SIM1_OP", "SIM2_OP", "SIM1_STA", "SIM2_STA", "SIM1_SIGNAL", "SIM2_SIGNAL", "WIFI_NAME", "WIFI_DBM"]
    body = f"keys={json.dumps({'keys': keys_list}, ensure_ascii=False)}"
    try:
        resp = _get_sync_client().post(
            f"http://{ip}/mgr",
            params={"a": "getHtmlData_index"},
            auth=httpx.DigestAuth(user, pw),
            content=body.encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if isinstance(data, dict) and data.get("success") and isinstance(data.get("data"), dict):
            return data["data"]
    except Exception:
        pass
    return None


def get_wifi_info(ip: str, user: str, pw: str) -> Dict[str, str]:
    _ensure_device_ip_allowed(ip)
    keys_list = ["WIFI_NAME", "WIFI_DBM"]
    body = f"keys={json.dumps({'keys': keys_list}, ensure_ascii=False)}"
    try:
        resp = _get_sync_client().post(
            f"http://{ip}/mgr",
            params={"a": "getHtmlData_index"},
            auth=httpx.DigestAuth(user, pw),
            content=body.encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and data.get("success") and isinstance(data.get("data"), dict):
                return {
                    "wifiName": data["data"].get("WIFI_NAME", ""),
                    "wifiDbm": data["data"].get("WIFI_DBM", ""),
                }
    except Exception:
        pass
    return {"wifiName": "", "wifiDbm": ""}


def read_device_config(ip: str, user: str, pw: str) -> Optional[str]:
    _ensure_device_ip_allowed(ip)
    body = f"keys={json.dumps({'keys': ['PROPF_1_1_1']}, ensure_ascii=False)}"
    try:
        resp = _get_sync_client().post(
            f"http://{ip}/mgr",
            params={"a": "getHtmlData_propfMgr"},
            auth=httpx.DigestAuth(user, pw),
            content=body.encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT + 5,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if isinstance(data, dict) and data.get("success") and isinstance(data.get("data"), dict):
            propf = data["data"].get("PROPF", "")
            if isinstance(propf, str):
                return propf
            return json.dumps(propf, ensure_ascii=False)
    except Exception:
        pass
    return None


def write_device_config(ip: str, user: str, pw: str, content: str) -> bool:
    _ensure_device_ip_allowed(ip)
    try:
        resp = _get_sync_client().post(
            f"http://{ip}/mgr",
            params={"a": "updateProf"},
            data={
                "hiddenWifi": "1",
                "hiddenAdminPwd": "1",
                "hiddenUserPwd": "1",
                "propf": content,
            },
            auth=httpx.DigestAuth(user, pw),
            timeout=TIMEOUT + 10,
        )
        return resp.status_code == 200
    except Exception:
        pass
    return False


def _device_conn_info(device: Device) -> Dict[str, Any]:
    return {
        "id": device.id,
        "ip": device.ip,
        "alias": device.alias or "",
        "grp": device.grp or "auto",
        "user": (device.user or DEFAULTUSER).strip(),
        "pw":   (device.passwd or DEFAULTPASS).strip(),
    }

def _device_to_dict(device: Device) -> Dict[str, Any]:
    return {
        "id":      device.id,
        "devId":   device.devId or "",
        "alias":   device.alias or "",
        "grp":     device.grp or "auto",
        "ip":      device.ip,
        "mac":     device.mac or "",
        "status":  device.status or "unknown",
        "lastSeen":device.lastSeen or 0,
        "created": device.created or "",
        "firmwareVersion": getattr(device, "firmware_version", "") or "",
        "sims": {
            "sim1": {"number": device.sim1number or "", "operator": device.sim1operator or "", "signal": device.sim1signal or 0, "label": device.sim1number or device.sim1operator or "SIM"},
            "sim2": {"number": device.sim2number or "", "operator": device.sim2operator or "", "signal": device.sim2signal or 0, "label": device.sim2number or device.sim2operator or "SIM"},
        },
        "wifiName": "",
        "wifiDbm": "",
    }


# FIX(P1#8): robust upsert that tolerates the UNIQUE(ip) constraint without
# silently deleting an unrelated row and without leaving the session in a
# rolled-back state.
def upsertdevice(db: Session, ip: str, mac: str, user: str, pw: str, grp: Optional[str] = None) -> Dict[str, Any]:
    data   = getdevicedata(ip, user, pw) or {}
    devid  = (data.get("DEV_ID") or "").strip() or None
    sim1num= (data.get("SIM1_PHNUM") or "").strip()
    sim2num= (data.get("SIM2_PHNUM") or "").strip()
    sim1op = (data.get("SIM1_OP") or "").strip() or _bm_op_from_sta(data.get("SIM1_STA") or "")
    sim2op = (data.get("SIM2_OP") or "").strip() or _bm_op_from_sta(data.get("SIM2_STA") or "")
    sim1sig= int(data.get("SIM1_SIGNAL") or 0)
    sim2sig= int(data.get("SIM2_SIGNAL") or 0)
    fw_ver = (data.get("DEV_VER") or "").strip()
    mac    = (mac or "").strip().upper() or None

    device: Optional[Device] = None
    if devid:
        device = db.query(Device).filter(Device.devId == devid).first()
    if not device and mac:
        device = db.query(Device).filter(Device.mac == mac).first()
    if not device:
        device = db.query(Device).filter(Device.ip == ip).first()

    if device and device.ip != ip:
        other = db.query(Device).filter(Device.ip == ip).first()
        if other and other.id != device.id:
            # Another DB row already owns the target IP (DHCP rotation).
            # Clear its ip to release the UNIQUE slot before reassigning.
            other.ip = f"__stale_{other.id}_{nowts()}"
            try:
                db.flush()
            except Exception:
                db.rollback()
                return _device_to_dict(device)

    if device:
        device.devId = devid if devid else device.devId
        if grp is not None and str(grp).strip():
            device.grp = grp
        device.ip          = ip
        device.mac         = mac if mac else (device.mac or None)
        device.user        = user
        device.passwd      = pw
        device.status      = "online"
        device.lastSeen    = nowts()
        device.sim1number  = sim1num
        device.sim1operator= sim1op
        device.sim1signal  = sim1sig
        device.sim2number  = sim2num
        device.sim2operator= sim2op
        device.sim2signal  = sim2sig
        if fw_ver:
            device.firmware_version = fw_ver
    else:
        device = Device(
            devId=devid, grp=(grp if grp is not None and str(grp).strip() else "auto"),
            ip=ip, mac=mac, user=user, passwd=pw, status="online", lastSeen=nowts(),
            sim1number=sim1num, sim1operator=sim1op, sim1signal=sim1sig,
            sim2number=sim2num, sim2operator=sim2op, sim2signal=sim2sig,
            firmware_version=fw_ver,
            created=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        db.add(device)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("upsert %s failed: %s", ip, exc)
        return {"ip": ip, "error": f"数据库写入失败: {exc}"}
    db.refresh(device)
    return _device_to_dict(device)


# FIX(N1): revert to a pure DB read. The previous implementation performed a
# blocking HTTP call per device (O(N) outbound requests per /api/devices hit,
# plus an SSRF amplifier); real-time status now happens via explicit
# /detail / /refresh endpoints or the scan flow.
def listdevices(db: Session) -> List[Dict[str, Any]]:
    devices = db.query(Device).order_by(Device.created.desc(), Device.id.desc()).all()
    return [_device_to_dict(d) for d in devices]


def getallnumbers(db: Session, group: str = "") -> List[Dict[str, Any]]:
    # FIX(P2#7, Devin Review #8): optional group filter so the dashboard
    # SIM count can stay consistent with the group-filtered device counts.
    query = db.query(Device)
    gval = (group or "").strip()
    if gval and gval != "all":
        query = query.filter(Device.grp == gval)
    numbers = []
    for device in query.all():
        for num, op, slot in [(device.sim1number, device.sim1operator, 1), (device.sim2number, device.sim2operator, 2)]:
            if num and num.strip():
                numbers.append({"deviceId": device.id, "deviceName": device.devId or device.ip,
                                "ip": device.ip, "grp": device.grp or "",
                                "number": num.strip(), "operator": op or "", "slot": slot})
    return numbers




# ── Register extracted route modules ─────────────────────────────────────────
from backend.routes.auth import router as auth_router
from backend.routes.devices import router as devices_router
from backend.routes.scan import router as scan_router
from backend.routes.config import router as config_router
from backend.routes import auth as _auth_mod
from backend.routes import devices as _devices_mod
from backend.routes import scan as _scan_mod
from backend.routes import config as _config_mod

# Inject shared helpers into route modules
_auth_mod.inject(
    client_ip=_client_ip,
    login_limiter_ip=_login_limiter_ip,
    login_limiter_user=_login_limiter_user,
    check_login_credentials=_check_login_credentials,
    extract_request_token=_extract_request_token,
    set_auth_cookies=_set_auth_cookies,
    clear_auth_cookies=_clear_auth_cookies,
    audit=_audit,
    bm_login_failure=_bm_login_failure,
    csrf_for_token=_csrf_for_token,
    session_local=SessionLocal,
)

_devices_mod.inject(
    get_sync_client=_get_sync_client,
    get_shared_executor=_get_shared_executor,
    ensure_device_ip_allowed=_is_device_ip_allowed,
    ensure_device_ip_allowed_raise=_ensure_device_ip_allowed,
    is_target_device=istargetdevice,
    get_device_data=getdevicedata,
    get_wifi_info_fn=get_wifi_info,
    read_device_config_fn=read_device_config,
    write_device_config_fn=write_device_config,
    device_to_dict=_device_to_dict,
    device_conn_info=_device_conn_info,
    upsert_device=upsertdevice,
    audit=_audit,
    validate_phone=_validate_phone,
    validate_sms_content=_validate_sms_content,
    sms_limiter=_sms_limiter,
    dial_limiter=_dial_limiter,
    ota_limiter=_ota_limiter,
    client_ip=_client_ip,
    check_login_credentials=_check_login_credentials,
)

_scan_mod.inject(
    guessipv4cidr=guessipv4cidr,
    prewarm_neighbors=prewarm_neighbors,
    getarptable=getarptable,
    tcp_port_open=_tcp_port_open,
    get_shared_executor=_get_shared_executor,
    is_target_device=istargetdevice,
    ensure_device_ip_allowed_raise=_ensure_device_ip_allowed,
    upsert_device=upsertdevice,
    audit=_audit,
)

_config_mod.inject(
    get_sync_client=_get_sync_client,
    get_shared_executor=_get_shared_executor,
    ensure_device_ip_allowed_raise=_ensure_device_ip_allowed,
    is_target_device=istargetdevice,
    read_device_config_fn=read_device_config,
    write_device_config_fn=write_device_config,
    device_conn_info=_device_conn_info,
    audit=_audit,
)

app.include_router(auth_router)
app.include_router(devices_router)
app.include_router(scan_router)
app.include_router(config_router)

# Keep shared helpers that are still needed by main.py
PHONE_RE = re.compile(r"^\+?[0-9]{5,15}$")

from pydantic import BaseModel, field_validator
from typing import Any, Dict, List, Optional, Tuple

class DirectDialReq(BaseModel):
    deviceId: int
    slot: int
    phone: str
    tts: str = ""
    duration: int = 175
    tts_times: int = 2
    tts_pause: int = 1
    after_action: int = 1

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v):
        v = (v or "").strip()
        if not v or not PHONE_RE.match(v):
            raise ValueError("手机号格式不正确")
        return v

# ── Device query helpers (used by routes) ────────────────────────────────────

def getallnumbers(db, group: str = "") -> list:
    from backend.db import Device
    numbers = []
    for device in db.query(Device).all():
        if group and group != "all" and device.grp != group:
            continue
        for num, op, slot in [(device.sim1number, device.sim1operator, 1), (device.sim2number, device.sim2operator, 2)]:
            if num and num.strip():
                numbers.append({
                    "deviceId": device.id, "deviceName": device.devId or device.ip,
                    "ip": device.ip, "number": num.strip(), "operator": op or "", "slot": slot,
                })
    return numbers

def _apply_devices_filter(query, q: str, group: str):
    from backend.db import Device
    qval = (q or "").strip().lower()
    if qval:
        like = f"%{_escape_like(qval)}%"
        query = query.filter(
            (Device.ip.ilike(like, escape="\\")) |
            (Device.mac.ilike(like, escape="\\")) |
            (Device.devId.ilike(like, escape="\\")) |
            (Device.alias.ilike(like, escape="\\")) |
            (Device.sim1number.ilike(like, escape="\\")) |
            (Device.sim2number.ilike(like, escape="\\")) |
            (Device.sim1operator.ilike(like, escape="\\")) |
            (Device.sim2operator.ilike(like, escape="\\"))
        )
    gval = (group or "").strip()
    if gval and gval != "all":
        query = query.filter(Device.grp == gval)
    return query

def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

# ── OTA tasks (used by devices router) ───────────────────────────────────────

def _ota_check(ip: str, user: str, pw: str) -> dict:
    _ensure_device_ip_allowed(ip)
    resp = _get_sync_client().get(
        f"http://{ip}/ota",
        params={"a": "chkNewVer"},
        auth=httpx.DigestAuth(user, pw),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    return data if isinstance(data, dict) else {}

def check_ota_task(device_id: int) -> dict:
    from backend.db import Device, SessionLocal as _SL
    db = _SL()
    try:
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            return {"id": device_id, "ok": False, "error": "设备不存在"}
        ip = device.ip
        user = (device.user or DEFAULTUSER).strip()
        pw = (device.passwd or DEFAULTPASS).strip()
        try:
            data = _ota_check(ip, user, pw)
        except HTTPException as exc:
            return {"id": device.id, "ip": ip, "ok": False, "error": exc.detail}
        except Exception as exc:
            return {"id": device.id, "ip": ip, "ok": False, "error": str(exc)}
        cur_ver = str(data.get("curVer", "") or "")
        new_ver = str(data.get("newVer", "") or "")
        if cur_ver:
            device.firmware_version = cur_ver
            try:
                db.commit()
            except Exception:
                db.rollback()
        return {
            "id": device.id, "ip": ip, "ok": True,
            "hasUpdate": bool(data.get("hasUpdate", False)) or (bool(new_ver) and new_ver != cur_ver),
            "currentVer": cur_ver, "newVer": new_ver,
        }
    finally:
        db.close()

def upgrade_ota_task(device_id: int) -> dict:
    from backend.db import Device, SessionLocal as _SL
    db = _SL()
    try:
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            return {"id": device_id, "ok": False, "error": "设备不存在"}
        ip = device.ip
        user = (device.user or DEFAULTUSER).strip()
        pw = (device.passwd or DEFAULTPASS).strip()
        try:
            data = _ota_check(ip, user, pw)
            cur_ver = str(data.get("curVer", "") or "")
            new_ver = str(data.get("newVer", "") or "")
            if cur_ver:
                device.firmware_version = cur_ver
                try:
                    db.commit()
                except Exception:
                    db.rollback()
            if not new_ver or new_ver == cur_ver:
                return {"id": device.id, "ip": ip, "ok": False, "error": "已是最新版本"}
            upgrade_resp = _get_sync_client().get(
                f"http://{ip}/ota",
                params={"a": "updOtaOnline"},
                auth=httpx.DigestAuth(user, pw),
                timeout=TIMEOUT,
            )
            return {"id": device.id, "ip": ip, "ok": upgrade_resp.status_code == 200, "newVer": new_ver}
        except HTTPException as exc:
            return {"id": device.id, "ip": ip, "ok": False, "error": exc.detail}
        except Exception as exc:
            return {"id": device.id, "ip": ip, "ok": False, "error": str(exc)}
    finally:
        db.close()

def ensure_device_token(db, device) -> str:
    token = (getattr(device, "token", "") or "").strip()
    if token:
        return token
    user = (getattr(device, "user", "") or DEFAULTUSER).strip()
    pw = (getattr(device, "passwd", "") or DEFAULTPASS).strip()
    _ensure_device_ip_allowed(device.ip)
    ok, _ = istargetdevice(device.ip, user, pw)
    if not ok:
        raise HTTPException(status_code=400, detail="Device authentication failed")
    token = fetch_device_token(device.ip, user, pw)
    if not token:
        raise HTTPException(status_code=400, detail="Failed to fetch token")
    try:
        device.token = token
        db.commit()
    except Exception:
        pass
    return token

def fetch_device_token(ip: str, user: str, pw: str) -> str:
    _ensure_device_ip_allowed(ip)
    body = b"keys=%7B%22keys%22%3A%5B%22TOKEN%22%5D%7D"
    resp = _get_sync_client().post(
        f"http://{ip}/mgr",
        params={"a": "getHtmlData_passwdMgr"},
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        auth=httpx.DigestAuth(user, pw),
        timeout=TIMEOUT + 5,
    )
    resp.raise_for_status()
    payload = resp.json()
    token = (payload.get("data", {}) or {}).get("TOKEN", "") or ""
    return re.sub(r"<[^>]+>", "", str(token)).strip()
