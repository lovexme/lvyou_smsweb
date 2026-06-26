import asyncio
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

import hashlib
import hmac
import logging
import uuid as _uuid

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
)
# FIX(P2#4 refactor): shared HTTP client/executor, audit logger, rate limiter
# and device-communication primitives now live in dedicated modules. main.py
# re-imports them under their original names so the route-module dependency
# injection and the lazy `from backend.main import ...` call sites are
# unchanged.
from backend.http_client import (
    get_sync_client as _get_sync_client,
    get_shared_executor as _get_shared_executor,
    init_runtime as _init_runtime,
    shutdown_runtime as _shutdown_runtime,
)
from backend.audit import audit as _audit
from backend.ratelimit import RateLimiter, max_period_seen as _rate_max_period
from backend.device_client import (
    ensure_device_ip_allowed as _ensure_device_ip_allowed,
    istargetdevice,
    getdevicedata,
    get_wifi_info,
    read_device_config,
    write_device_config,
    ota_check as _ota_check,
    check_ota_task,
    upgrade_ota_task,
    ensure_device_token,
    fetch_device_token,
)
from backend.db import cleanup_rate_events as _cleanup_rate_events

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


# FIX(M3/H2 refactor): RateLimiter now lives in backend.ratelimit and is backed
# by SQLite so the v4 + v6 processes share one budget and idle keys do not leak
# memory. Each instance carries a scope string used as the event namespace.
_sms_limiter  = RateLimiter("sms",  int(os.environ.get("BMSMSRATELIMIT",  "10")), float(os.environ.get("BMSMSRATEPERIOD",  "60")))
_dial_limiter = RateLimiter("dial", int(os.environ.get("BMDIALRATELIMIT",  "5")), float(os.environ.get("BMDIALRATEPERIOD", "60")))
# FIX(P2#2): two-dimensional login rate limit. The IP limiter catches one
# attacker probing many usernames; the username limiter catches a
# distributed bruteforce of a single known account from many addresses.
# Both are count-failures-only so legitimate users with one or two typos
# are not locked out alongside attackers.
_login_limiter_ip = RateLimiter(
    "login_ip",
    int(os.environ.get("BMLOGINRATELIMIT", "5")),
    float(os.environ.get("BMLOGINRATEPERIOD", "60")),
)
_login_limiter_user = RateLimiter(
    "login_user",
    int(os.environ.get("BMLOGINUSERRATELIMIT", "10")),
    float(os.environ.get("BMLOGINUSERRATEPERIOD", "600")),
)
# FIX(N5): OTA batch rate limiter (per user), prevents using it as an internal reboot-storm
_ota_limiter  = RateLimiter("ota", int(os.environ.get("BMOTARATELIMIT",  "4")), float(os.environ.get("BMOTARATEPERIOD",  "60")))

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


# FIX(P2#3 / P2#4 refactor): the structured audit logger (JSON file sink +
# rotation) now lives in backend.audit; `_audit` is re-imported above.


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


# NOTE: the live scan state (ScanState class, the ``_active_scans``
# registry and the TTL-based cleanup) lives in backend.routes.scan, which
# is where scans are actually created and served. This module used to
# carry an identical-but-unused copy; the periodic cleanup loop below
# cleaned *that* dead dict while finished scans piled up in the route
# module's registry. The duplicate has been removed and the loop now
# calls the scan router's cleanup (see _scan_cleanup_loop).


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


# ── Background cleanup ───────────────────────────────────────────────────────
# The shared httpx client + executor are owned by backend.http_client and
# re-imported above as _get_sync_client / _get_shared_executor / _init_runtime.
_cleanup_task: Optional["asyncio.Task"] = None

# FIX(M4): __stale_<id>_<ts> ghost rows are created when a DHCP lease moves an IP
# onto a device that already exists under another identity -- the old row's ip is
# renamed to free the UNIQUE slot. Drop those orphans once they have not been
# seen for STALE_DEVICE_TTL seconds (default 7 days) so they don't pile up.
STALE_DEVICE_TTL = int(os.environ.get("BMSTALEDEVICETTL", str(7 * 86400)))


def _cleanup_stale_devices() -> None:
    db = SessionLocal()
    try:
        cutoff = nowts() - STALE_DEVICE_TTL
        deleted = (
            db.query(Device)
            .filter(Device.ip.like("__stale\\_%", escape="\\"), Device.lastSeen < cutoff)
            .delete(synchronize_session=False)
        )
        if deleted:
            db.commit()
            logger.info("cleaned %d stale device rows", deleted)
        else:
            db.rollback()
    except Exception:
        db.rollback()
        logger.debug("stale device cleanup failed", exc_info=True)
    finally:
        db.close()


async def _scan_cleanup_loop() -> None:
    while True:
        try:
            # Clean the route module's real scan registry (imported lazily
            # to avoid an import cycle at module load time).
            from backend.routes.scan import cleanup_old_scans as _cleanup_old_scans
            _cleanup_old_scans()
            _cleanup_expired_tokens()
            # FIX(H2/M3): prune rate-limit events past the widest window.
            _cleanup_rate_events(int(_rate_max_period()))
            # FIX(M4): prune orphaned __stale_ device rows.
            _cleanup_stale_devices()
        except Exception:
            logger.debug("background cleanup error", exc_info=True)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cleanup_task
    # FIX(P0#1): refuse to start with an unset / default BMUIPASS.
    _validate_startup_security()
    # FIX(P1#9/P1#10/P2#4): the shared connection-pooled client + executor are
    # created here and shared with security.prewarm_neighbors inside init_runtime.
    sync_client, executor = _init_runtime()
    app.state.sync_http_client = sync_client
    app.state.executor = executor
    # FIX(P1#12): periodic cleanup task for finished scans, expired tokens,
    # rate-limit events and stale device rows.
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
        _shutdown_runtime()


app = FastAPI(title="Board LAN Hub", version="5.1", lifespan=lifespan)
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
# default CIDR guess) live in ``backend.security``; the thin HTTP-aware
# wrapper that converts allowlist failures into HTTPException
# (_ensure_device_ip_allowed) and every device-communication primitive
# (istargetdevice / getdevicedata / get_wifi_info / read_device_config /
# write_device_config / OTA / token) now live in backend.device_client and
# are re-imported above under their original names.
def _bm_op_from_sta(sta: str) -> str:
    return (sta or "").strip()


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

# ── Device query helpers (used by routes) ────────────────────────────────────
# NOTE: getallnumbers() is defined once, earlier in this module. It filters
# by group in SQL and includes the ``grp`` field. A second definition used
# to live here and shadowed it with an inferior in-Python filter that also
# dropped the ``grp`` field -- removed. PHONE_RE is likewise defined once,
# near the top of the module.

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

# ── OTA tasks + device token ─────────────────────────────────────────────────
# _ota_check / check_ota_task / upgrade_ota_task / ensure_device_token /
# fetch_device_token now live in backend.device_client and are re-imported above
# under their original names (the devices router imports them lazily from here).
