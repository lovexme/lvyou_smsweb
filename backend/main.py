import asyncio
import socket
import threading
import json
import os
import re
import secrets
import subprocess
import time
from datetime import datetime
from ipaddress import ip_address, ip_network, IPv4Network, IPv4Address, IPv6Address
from typing import Any, Dict, List, Optional, Tuple
from itertools import islice
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Depends, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from sqlalchemy import create_engine, Column, Integer, String, Text, BigInteger, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

import concurrent.futures
import hmac
import logging
import uuid as _uuid
from collections import defaultdict
from threading import Lock as _Lock

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("board-manager")
logger.setLevel(logging.DEBUG if os.environ.get("BMDEBUG") else logging.INFO)

DBPATH      = os.environ.get("BMDB",      "/opt/board-manager/data/data.db")
STATICDIR   = os.environ.get("BMSTATIC",  "/opt/board-manager/static")
DEFAULTUSER = os.environ.get("BMDEVUSER", "admin")
DEFAULTPASS = os.environ.get("BMDEVPASS", "admin")
TIMEOUT            = float(os.environ.get("BMHTTPTIMEOUT",    "5.0"))
CONCURRENCY        = int(os.environ.get("BMSCANCONCURRENCY", "64"))
TCP_CONCURRENCY    = int(os.environ.get("BMTCPCONCURRENCY",  "128"))
TCP_TIMEOUT        = float(os.environ.get("BMTCPTIMEOUT",    "0.3"))
CIDRFALLBACKLIMIT  = int(os.environ.get("BMCIDRFALLBACKLIMIT","1024"))
SCAN_RETRIES       = int(os.environ.get("BMSCANRETRIES",     "3"))
SCAN_RETRY_SLEEP_MS= int(os.environ.get("BMSCANRETRYSLEEPMS","300"))
SCAN_TTL           = int(os.environ.get("BMSCANTTL",         str(3600)))
UIUSER             = os.environ.get("BMUIUSER",  "admin")
UIPASS             = os.environ.get("BMUIPASS",  "admin")
TOKEN_TTL_SECONDS  = int(os.environ.get("BMTOKENTTL", str(8 * 60 * 60)))
PREWARM_CONCURRENCY = int(os.environ.get("BMPREWARMCONCURRENCY", "64"))
OTA_BATCH_MAX      = int(os.environ.get("BMOTABATCHMAX",       "64"))
CONFIG_MAX_CHARS   = int(os.environ.get("BMCONFIGMAXCHARS",    "524288"))
TRUSTED_PROXY_HOPS = int(os.environ.get("BMTRUSTEDPROXYHOPS",  "0"))

# FIX(P1#17): magic string -> named constant
FORWARD_METHOD_BASIC = "99"

Base = declarative_base()
engine = create_engine(
    f"sqlite:///{DBPATH}",
    pool_pre_ping=True, pool_recycle=3600,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Device(Base):
    __tablename__ = "devices"
    id           = Column(Integer, primary_key=True, index=True)
    devId        = Column(String(128), unique=True, nullable=True)
    grp          = Column(String(64),  default="auto")
    ip           = Column(String(45),  unique=True, index=True, nullable=False)
    mac          = Column(String(32),  unique=True, nullable=True, default="")
    user         = Column(String(64),  default="")
    passwd       = Column(String(64),  default="")
    status       = Column(String(32),  default="unknown")
    lastSeen     = Column(BigInteger,  default=0)
    sim1number   = Column(String(32),  default="")
    sim1operator = Column(String(64),  default="")
    sim1signal   = Column(Integer,     default=0)
    sim2number   = Column(String(32),  default="")
    sim2operator = Column(String(64),  default="")
    sim2signal   = Column(Integer,     default=0)
    token        = Column(Text,        default="")
    # FIX(N3): dedicated column for firmware version so OTA check never
    # overwrites the device's stable identifier (devId).
    firmware_version = Column(String(64), default="")
    alias        = Column(String(128), default="")
    created      = Column(String(32),  default="")


# FIX(P0#2): persist auth tokens to SQLite so that multiple uvicorn processes
# (e.g. the v4 and v6 listeners) share the same session store.
class AuthToken(Base):
    __tablename__ = "auth_tokens"
    token    = Column(String(128), primary_key=True)
    username = Column(String(64),  default="")
    exp      = Column(BigInteger,  default=0, index=True)


Base.metadata.create_all(bind=engine)


class RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self._max    = max_calls
        self._period = period
        self._hits: Dict[str, list] = defaultdict(list)
        self._lock   = _Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            window = [t for t in self._hits[key] if now - t < self._period]
            if len(window) >= self._max:
                self._hits[key] = window
                return False
            window.append(now)
            self._hits[key] = window
            return True

    def remaining(self, key: str) -> int:
        now = time.time()
        with self._lock:
            window = [t for t in self._hits.get(key, []) if now - t < self._period]
            return max(0, self._max - len(window))


_sms_limiter  = RateLimiter(int(os.environ.get("BMSMSRATELIMIT",  "10")), float(os.environ.get("BMSMSRATEPERIOD",  "60")))
_dial_limiter = RateLimiter(int(os.environ.get("BMDIALRATELIMIT",  "5")), float(os.environ.get("BMDIALRATEPERIOD", "60")))
# FIX: login brute-force rate limiter
_login_limiter= RateLimiter(int(os.environ.get("BMLOGINRATELIMIT", "5")), float(os.environ.get("BMLOGINRATEPERIOD","60")))
# FIX(N5): OTA batch rate limiter (per user), prevents using it as an internal reboot-storm
_ota_limiter  = RateLimiter(int(os.environ.get("BMOTARATELIMIT",  "4")), float(os.environ.get("BMOTARATEPERIOD",  "60")))

PHONE_RE    = re.compile(r"^\+?[0-9]{5,15}$")
SMS_MAX_LEN = int(os.environ.get("BMSMSMAXLEN", "500"))


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


_audit_logger = logging.getLogger("audit")


def _audit(action: str, user: str = "-", detail: str = ""):
    _audit_logger.info("action=%s user=%s detail=%s", action, user, detail)


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


def _run_migrations():
    """Idempotent ALTER TABLE migrations for columns added across versions."""
    alters = [
        ("devices", "token",            "TEXT DEFAULT ''"),
        ("devices", "sim1signal",       "INTEGER DEFAULT 0"),
        ("devices", "sim2signal",       "INTEGER DEFAULT 0"),
        ("devices", "firmware_version", "VARCHAR(64) DEFAULT ''"),
    ]
    with engine.connect() as conn:
        for table, col, coltype in alters:
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            cols = [r[1] for r in rows]
            if col not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))
        conn.commit()


_run_migrations()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def nowts() -> int:
    return int(time.time())


# ── Token persistence (SQLite-backed, shared across processes) ───────────────
def _cleanup_expired_tokens() -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM auth_tokens WHERE exp <= :n"), {"n": nowts()})
    except Exception:
        logger.debug("token cleanup failed", exc_info=True)


def _get_token_record(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT username, exp FROM auth_tokens WHERE token = :t"),
                {"t": token},
            ).first()
            if not row:
                return None
            return {"username": row[0] or "", "exp": int(row[1] or 0)}
    except Exception:
        logger.debug("token lookup failed", exc_info=True)
        return None


def _insert_token(token: str, username: str, exp: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT OR REPLACE INTO auth_tokens(token, username, exp) VALUES(:t, :u, :e)"),
            {"t": token, "u": username, "e": exp},
        )


def _delete_token(token: str) -> None:
    if not token:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM auth_tokens WHERE token = :t"), {"t": token})
    except Exception:
        logger.debug("token delete failed", exc_info=True)


def _issue_token(username: str) -> str:
    _cleanup_expired_tokens()
    token = secrets.token_urlsafe(32)
    _insert_token(token, username, nowts() + TOKEN_TTL_SECONDS)
    return token


def _extract_bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "").strip()
    if not auth.startswith("Bearer "):
        return ""
    return auth[7:].strip()


def _unauthorized_json(detail: str = "未登录或登录已失效") -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": detail})


def _require_token(request: Request) -> Dict[str, Any]:
    token = _extract_bearer_token(request)
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


def _client_ip(request: Request) -> str:
    """Resolve real client IP honouring X-Forwarded-For when behind a trusted
    reverse proxy. Set BMTRUSTEDPROXYHOPS >= 1 to read the header."""
    if TRUSTED_PROXY_HOPS > 0:
        xff = request.headers.get("x-forwarded-for", "") or request.headers.get("X-Forwarded-For", "")
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts:
                idx = max(0, len(parts) - TRUSTED_PROXY_HOPS)
                return parts[idx]
        real = request.headers.get("x-real-ip", "") or request.headers.get("X-Real-IP", "")
        if real:
            return real.strip()
    return request.client.host if request.client else "unknown"


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
    # FIX(P1#9): one sync client for the whole process, connection pooled.
    _sync_client = httpx.Client(
        timeout=TIMEOUT,
        limits=httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=20),
        follow_redirects=False,
    )
    # FIX(P1#10): one ThreadPoolExecutor for all batch endpoints / scans.
    _shared_executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(CONCURRENCY, 32))
    app.state.http_client = httpx.AsyncClient(
        timeout=TIMEOUT,
        limits=httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=20),
        follow_redirects=False,
    )
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
            await app.state.http_client.aclose()
        except Exception:
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
        allow_headers=["Authorization", "Content-Type"],
    )


_configure_cors(app)


# FIX(P0#1): expose /api/health so container/compose HEALTHCHECK works without
# needing a Bearer token. The endpoint returns only liveness info.
_PUBLIC_PATHS = {"/", "/api/login", "/api/health"}


@app.middleware("http")
async def token_auth_mw(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static/") or path in _PUBLIC_PATHS:
        return await call_next(request)
    try:
        _require_token(request)
    except HTTPException as exc:
        return _unauthorized_json(exc.detail)
    return await call_next(request)


os.makedirs(STATICDIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATICDIR), name="static")


@app.get("/")
def uiindex():
    index_path = os.path.join(STATICDIR, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="UI not built")
    return FileResponse(index_path)


# FIX(P0#5): never invoke a shell. Each helper below feeds argv directly to
# subprocess; no user-controllable values are passed.
def _run(argv: List[str], timeout: float = 3.0) -> str:
    return subprocess.check_output(argv, stderr=subprocess.DEVNULL, text=True, timeout=timeout).strip()


def guessipv4cidr() -> str:
    try:
        route_text = _run(["ip", "-4", "route", "show", "default"])
        for line in route_text.splitlines():
            match = re.search(r"dev\s+(\S+)", line)
            if not match:
                continue
            iface = match.group(1)
            addr_text = _run(["ip", "-4", "addr", "show", "dev", iface])
            for addr_line in addr_text.splitlines():
                m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+/\d+)", addr_line)
                if m:
                    net = ip_network(m.group(1), strict=False)
                    if isinstance(net, IPv4Network):
                        return f"{net.network_address}/{net.prefixlen}"
            break
    except Exception:
        pass
    try:
        txt = _run(["ip", "-o", "-4", "addr", "show"])
        for line in txt.splitlines():
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            iface, cidr = parts[1], parts[3]
            if iface == "lo":
                continue
            net = ip_network(cidr, strict=False)
            if isinstance(net, IPv4Network):
                return f"{net.network_address}/{net.prefixlen}"
    except Exception:
        pass
    return "192.168.1.0/24"


def _local_ipv4_networks() -> List[IPv4Network]:
    nets: List[IPv4Network] = []
    try:
        txt = _run(["ip", "-o", "-4", "addr", "show"])
        for line in txt.splitlines():
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            iface, cidr = parts[1], parts[3]
            if iface == "lo":
                continue
            try:
                net = ip_network(cidr, strict=False)
                if isinstance(net, IPv4Network):
                    nets.append(net)
            except Exception:
                continue
    except Exception:
        pass
    return nets


# FIX(P0#3): whitelist before any outbound request to a device. A device.ip
# value either comes from scanning a local subnet or from operator input; in
# neither case should the backend be tricked into fetching public / metadata
# endpoints on its behalf. We accept only private addresses and — more
# strictly — IPs that fall in one of the local interface subnets.
def _is_device_ip_allowed(ip: str) -> bool:
    try:
        addr = ip_address(ip)
    except Exception:
        return False
    if isinstance(addr, IPv4Address):
        if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified or addr.is_reserved:
            return False
        if not addr.is_private:
            return False
        nets = _local_ipv4_networks()
        if not nets:
            return True
        for net in nets:
            if addr in net:
                return True
        return False
    if isinstance(addr, IPv6Address):
        if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified or addr.is_reserved:
            return False
        return addr.is_private or addr.is_site_local
    return False


def _ensure_device_ip_allowed(ip: str) -> None:
    if not _is_device_ip_allowed(ip):
        logger.warning("blocked outbound device request to non-whitelisted ip: %s", ip)
        raise HTTPException(status_code=400, detail="设备 IP 不在允许的内网范围内")


def getarptable() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open("/proc/net/arp") as handle:
            for line in handle.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 4:
                    ip  = parts[0].strip()
                    mac = parts[3].strip().upper()
                    if mac and mac != "00:00:00:00:00:00" and ":" in mac:
                        out[ip] = mac
    except Exception:
        pass
    try:
        txt = subprocess.check_output(["ip", "neigh", "show"], text=True, stderr=subprocess.DEVNULL)
        for line in txt.splitlines():
            parts = line.split()
            if len(parts) >= 5 and "lladdr" in parts:
                ip  = parts[0].strip()
                mac = parts[parts.index("lladdr") + 1].strip().upper()
                if mac and mac != "00:00:00:00:00:00" and ":" in mac:
                    out[ip] = mac
    except Exception:
        pass
    return out


# FIX(P1#11): cap concurrent ping subprocesses, avoiding 1024 fork()s at once.
def prewarm_neighbors(net: IPv4Network) -> None:
    try:
        hosts = [str(host) for host in islice(net.hosts(), CIDRFALLBACKLIMIT)]
        sem = threading.Semaphore(max(1, PREWARM_CONCURRENCY))
        deadline = time.time() + 8

        def _ping(ip_str: str) -> None:
            if time.time() >= deadline:
                return
            with sem:
                try:
                    subprocess.run(
                        ["ping", "-c", "1", "-W", "1", ip_str],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=max(0.5, deadline - time.time()),
                    )
                except Exception:
                    pass

        threads: List[threading.Thread] = []
        for ip in hosts:
            t = threading.Thread(target=_ping, args=(ip,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=max(0.1, deadline - time.time()))
        time.sleep(0.2)
    except Exception:
        pass


def _tcp_port_open(ip: str, port: int = 80) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=TCP_TIMEOUT):
            return True
    except Exception:
        return False


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
        device.mac         = mac if mac else device.mac
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
            ip=ip, mac=(mac or ""), user=user, passwd=pw, status="online", lastSeen=nowts(),
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
        return {"ip": ip, "error": "数据库写入失败"}
    db.refresh(device)
    return _device_to_dict(device)


# FIX(N1): revert to a pure DB read. The previous implementation performed a
# blocking HTTP call per device (O(N) outbound requests per /api/devices hit,
# plus an SSRF amplifier); real-time status now happens via explicit
# /detail / /refresh endpoints or the scan flow.
def listdevices(db: Session) -> List[Dict[str, Any]]:
    devices = db.query(Device).order_by(Device.created.desc(), Device.id.desc()).all()
    return [_device_to_dict(d) for d in devices]


def getallnumbers(db: Session) -> List[Dict[str, Any]]:
    numbers = []
    for device in db.query(Device).all():
        for num, op, slot in [(device.sim1number, device.sim1operator, 1), (device.sim2number, device.sim2operator, 2)]:
            if num and num.strip():
                numbers.append({"deviceId": device.id, "deviceName": device.devId or device.ip,
                                 "ip": device.ip, "number": num.strip(), "operator": op or "", "slot": slot})
    return numbers


# ── Pydantic Models ───────────────────────────────────────────────────────────
class LoginReq(BaseModel):
    username: str
    password: str


class DirectSmsReq(BaseModel):
    deviceId: int
    phone: str
    content: str
    slot: int

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v):
        v = (v or "").strip()
        if not v or not PHONE_RE.match(v):
            raise ValueError("手机号格式不正确")
        return v

    @field_validator("content")
    @classmethod
    def _check_content(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("短信内容不能为空")
        if len(v) > SMS_MAX_LEN:
            raise ValueError(f"短信内容超出长度限制（最多{SMS_MAX_LEN}字）")
        return v


class DirectDialReq(BaseModel):
    deviceId:     int
    slot:         int
    phone:        str
    tts:          str = ""
    duration:     int = 175
    tts_times:    int = 2
    tts_pause:    int = 1
    after_action: int = 1

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v):
        v = (v or "").strip()
        if not v or not PHONE_RE.match(v):
            raise ValueError("手机号格式不正确")
        return v


class AliasReq(BaseModel):
    alias: str


class GroupReq(BaseModel):
    group: str


class BatchDeleteReq(BaseModel):
    device_ids: List[int]


class BatchWifiReq(BaseModel):
    device_ids: List[int]
    ssid: str
    pwd:  str


class SimReq(BaseModel):
    sim1: str = ""
    sim2: str = ""


class BatchSimReq(BaseModel):
    device_ids: List[int]
    sim1: str = ""
    sim2: str = ""


class BatchConfigReadReq(BaseModel):
    device_ids: List[int]


class BatchConfigPreviewReq(BaseModel):
    device_ids:   List[int]
    pattern:      str
    replacement:  str = ""
    flags:        str = ""


class BatchConfigWriteReq(BaseModel):
    device_ids:   List[int]
    pattern:      str
    replacement:  str = ""
    flags:        str = ""


class BatchConfigPresetReq(BaseModel):
    device_ids: List[int]
    preset:     str = "clean_message_templates"


class BatchForwardReq(BaseModel):
    device_ids: List[int]
    forwardUrl:  str = ""
    notifyUrl:   str = ""


class EnhancedBatchForwardReq(BaseModel):
    device_ids:    List[int]
    forward_method:str
    forwardUrl:    str = ""
    notifyUrl:     str = ""
    deviceKey0:    str = ""
    deviceKey1:    str = ""
    deviceKey2:    str = ""
    smtpProvider:  str = ""
    smtpServer:    str = ""
    smtpPort:      str = ""
    smtpAccount:   str = ""
    smtpPassword:  str = ""
    smtpFromEmail: str = ""
    smtpToEmail:   str = ""
    smtpEncryption:str = ""
    webhookUrl1:   str = ""
    webhookUrl2:   str = ""
    webhookUrl3:   str = ""
    signKey1:      str = ""
    signKey2:      str = ""
    signKey3:      str = ""
    sc3ApiUrl:     str = ""
    sctSendKey:    str = ""
    PPToken:       str = ""
    PPChannel:     str = ""
    PPWebhook:     str = ""
    PPFriends:     str = ""
    PPGroupId:     str = ""
    WPappToken:    str = ""
    WPUID:         str = ""
    WPTopicId:     str = ""
    lyApiUrl:      str = ""


class ScanStartReq(BaseModel):
    cidr:     Optional[str] = None
    group:    Optional[str] = None
    user:     str = ""
    password: str = ""


# ── API Routes ──
@app.post("/api/login")
def api_login(req: LoginReq, request: Request):
    # FIX(P1#13): use X-Forwarded-For when running behind a trusted reverse
    # proxy so the login rate limiter keys on the real client IP.
    client_ip = _client_ip(request)
    if not _login_limiter.allow(client_ip):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")
    username = (req.username or "").strip()
    if not _check_login_credentials(username, req.password or ""):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = _issue_token(username)
    _audit("login", user=username, detail=f"ip={client_ip}")
    return {"ok": True, "token": token, "username": username, "expiresIn": TOKEN_TTL_SECONDS}


@app.post("/api/logout")
def api_logout(request: Request):
    token = _extract_bearer_token(request)
    if token:
        _delete_token(token)
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"status": "ok", "message": "Board LAN Hub API is running"}


@app.get("/api/devices")
def apidevices(page: int = Query(1, ge=1), page_size: int = Query(0, ge=0, le=500), db: Session = Depends(get_db)):
    query = db.query(Device).order_by(Device.created.desc(), Device.id.desc())
    if page_size > 0:
        total = query.count()
        items = query.offset((page - 1) * page_size).limit(page_size).all()
        return {"items": [_device_to_dict(d) for d in items], "total": total, "page": page,
                "page_size": page_size, "pages": (total + page_size - 1) // page_size}
    return [_device_to_dict(d) for d in query.all()]


@app.get("/api/numbers")
def apinumbers(page: int = Query(1, ge=1), page_size: int = Query(0, ge=0, le=500), db: Session = Depends(get_db)):
    all_nums = getallnumbers(db)
    if page_size > 0:
        total = len(all_nums)
        start = (page - 1) * page_size
        return {"items": all_nums[start:start + page_size], "total": total, "page": page,
                "page_size": page_size, "pages": (total + page_size - 1) // page_size}
    return all_nums


@app.get("/api/devices/{devid}/detail")
def api_device_detail(devid: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == devid).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    payload = _device_to_dict(device)
    payload["sim1number"]   = device.sim1number or ""
    payload["sim1operator"] = device.sim1operator or ""
    payload["sim1signal"]   = device.sim1signal or 0
    payload["sim2number"]   = device.sim2number or ""
    payload["sim2operator"] = device.sim2operator or ""
    payload["sim2signal"]   = device.sim2signal or 0
    sig_data = getdevicedata(device.ip, device.user or DEFAULTUSER, device.passwd or DEFAULTPASS) or {}
    payload["wifiName"] = sig_data.get("WIFI_NAME", "")
    payload["wifiDbm"]  = sig_data.get("WIFI_DBM", "")
    return {"device": payload, "forwardconfig": {}, "wifilist": []}


@app.post("/api/devices/{devid}/alias")
def api_set_alias(devid: int, req: AliasReq, db: Session = Depends(get_db)):
    alias = (req.alias or "").strip()
    if len(alias) > 24:
        raise HTTPException(status_code=400, detail="alias too long")
    device = db.query(Device).filter(Device.id == devid).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    device.alias = alias
    db.commit()
    return {"ok": True}


@app.post("/api/devices/{devid}/group")
def api_set_group(devid: int, req: GroupReq, db: Session = Depends(get_db)):
    group = (req.group or "").strip() or "auto"
    device = db.query(Device).filter(Device.id == devid).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    device.grp = group
    db.commit()
    return {"ok": True}


@app.delete("/api/devices/{dev_id}")
def deletedevice(dev_id: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == dev_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    db.delete(device)
    db.commit()
    return {"ok": True}


@app.post("/api/devices/batch/delete")
def api_batch_delete(req: BatchDeleteReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    deleted = 0
    for dev_id in req.device_ids:
        device = db.query(Device).filter(Device.id == dev_id).first()
        if device:
            db.delete(device)
            deleted += 1
    db.commit()
    return {"ok": True, "deleted": deleted}


def _safe_ip_in_net(ip: str, net: IPv4Network) -> bool:
    try:
        return ip_address(ip) in net
    except Exception:
        return False


def _scan_worker(cidr: str, group: Optional[str], user: str, password: str, state: ScanState):
    try:
        state.set_status("scanning", "解析网段...")
        net = ip_network(cidr, strict=False)

        state.set_progress("预热邻居...")
        prewarm_neighbors(net)
        arptable = getarptable()

        seen: set = set()
        iplist: List[str] = []
        arp_ips = [ip for ip in arptable if _safe_ip_in_net(ip, net)]
        for ip in arp_ips + [str(host) for host in islice(net.hosts(), CIDRFALLBACKLIMIT)]:
            if ip not in seen:
                seen.add(ip)
                iplist.append(ip)
            if len(iplist) >= CIDRFALLBACKLIMIT:
                break
        state.set_counts(total_ips=len(iplist))
        state.set_progress(f"TCP 探测 {len(iplist)} 个 IP...")

        open80: List[str] = []
        olock = threading.Lock()

        def _tcp(ip: str):
            if _tcp_port_open(ip, 80):
                with olock:
                    open80.append(ip)

        # FIX(P1#10): reuse the process-wide executor.
        executor = _get_shared_executor()
        list(executor.map(_tcp, iplist))
        state.set_counts(scanned=len(open80))
        state.set_progress(f"验证 {len(open80)} 台设备...")

        found: List[Dict[str, Any]] = []
        flock = threading.Lock()

        def _probe(ip: str):
            try:
                ok, _ = istargetdevice(ip, user, password)
            except HTTPException:
                return
            if ok:
                mac = arptable.get(ip, "")
                with flock:
                    found.append({"ip": ip, "mac": mac, "devId": "", "grp": group})

        list(executor.map(_probe, open80))

        state.set_results(found)
        state.set_progress(f"完成，发现 {len(found)} 台")
    except Exception as exc:
        state.set_status("error", f"扫描出错: {exc}")
        logger.error("scan error for %s: %s", cidr, exc, exc_info=True)
        return
    state.set_status("done")


def _run_scan_bg(scan_id: str, cidr: str, group: Optional[str], user: str, password: str):
    with _active_scans_lock:
        state = _active_scans.get(scan_id)
    if not state:
        return

    try:
        _scan_worker(cidr, group, user, password, state)

        if state.status == "error":
            state.mark_done()
            return

        state.set_status("saving", "保存设备到数据库...")
        db = SessionLocal()
        try:
            saved: List[Dict[str, Any]] = []
            for item in state.results:
                try:
                    d = upsertdevice(db, item["ip"], item["mac"], user, password, item.get("grp"))
                    saved.append(d)
                except Exception as exc:
                    logger.warning("save device %s failed: %s", item["ip"], exc)
            state.set_results(saved)
            state.set_status("done", f"完成，发现 {len(saved)} 台设备")
        finally:
            db.close()
    except Exception as exc:
        state.set_status("error", f"保存失败: {exc}")
        logger.error("scan save error: %s", exc, exc_info=True)
    finally:
        state.mark_done()


def _submit_scan(cidr: Optional[str], group: Optional[str], user: str, password: str, background_tasks: BackgroundTasks) -> str:
    if not cidr:
        cidr = guessipv4cidr()
    try:
        net = ip_network(cidr, strict=False)
        if not isinstance(net, IPv4Network):
            raise ValueError("only IPv4 supported")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无效的网段: {exc}")
    scan_id = _uuid.uuid4().hex[:12]
    state = ScanState()
    state.set_cidr(cidr)
    with _active_scans_lock:
        _active_scans[scan_id] = state
    background_tasks.add_task(_run_scan_bg, scan_id, cidr, group, user, password)
    _audit("scan_start", detail=f"cidr={cidr}")
    return scan_id


@app.post("/api/scan/start")
def scanstart(req: ScanStartReq, background_tasks: BackgroundTasks):
    user     = req.user.strip()     or DEFAULTUSER
    password = req.password.strip() or DEFAULTPASS
    scan_id  = _submit_scan(req.cidr, req.group, user, password, background_tasks)
    return {"ok": True, "scanId": scan_id}


@app.get("/api/scan/status/{scan_id}")
def scanstatus(scan_id: str):
    _cleanup_old_scans()
    with _active_scans_lock:
        state = _active_scans.get(scan_id)
    if not state:
        raise HTTPException(status_code=404, detail="扫描任务不存在")
    return state.to_dict()


@app.post("/api/sms/send-direct")
def smssenddirect(req: DirectSmsReq, request: Request, db: Session = Depends(get_db)):
    if req.slot not in (1, 2):
        raise HTTPException(status_code=400, detail="slot must be 1 or 2")
    phone   = _validate_phone(req.phone)
    content = _validate_sms_content(req.content)
    rl_key  = f"sms:{req.deviceId}"
    if not _sms_limiter.allow(rl_key):
        remaining = _sms_limiter.remaining(rl_key)
        raise HTTPException(status_code=429, detail=f"发送过于频繁，请稍后再试（剩余{remaining}次/分钟）")
    device = db.query(Device).filter(Device.id == req.deviceId).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    ip   = device.ip
    _ensure_device_ip_allowed(ip)
    user = (device.user   or DEFAULTUSER).strip()
    pw   = (device.passwd or DEFAULTPASS).strip()
    try:
        ok, _ = istargetdevice(ip, user, pw)
        if not ok:
            raise HTTPException(status_code=400, detail="设备认证失败")
        resp = _get_sync_client().get(
            f"http://{ip}/mgr",
            params={"a": "sendsms", "sid": str(req.slot), "phone": phone, "content": content},
            auth=httpx.DigestAuth(user, pw),
            timeout=TIMEOUT + 3,
        )
        if resp.status_code == 200:
            try:
                body = resp.json()
                if isinstance(body, dict) and body.get("success") is True:
                    _audit("sms_send", detail=f"device={req.deviceId} slot={req.slot} phone={phone[:4]}***")
                    return {"ok": True}
                return {"ok": False, "error": "设备返回发送失败"}
            except Exception:
                return {"ok": False, "error": "设备返回异常"}
        return {"ok": False, "error": f"设备通信失败 (HTTP {resp.status_code})"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("sms send error device=%s: %s", req.deviceId, exc, exc_info=True)
        return {"ok": False, "error": "短信发送失败，请稍后重试"}


def wifi_task_sync(device_info: Dict[str, Any], ssid: str, pwd: str) -> Dict[str, Any]:
    ip   = device_info["ip"]
    user = device_info["user"]
    pw   = device_info["pw"]
    try:
        _ensure_device_ip_allowed(ip)
        ok, _ = istargetdevice(ip, user, pw)
        if not ok:
            return {"id": device_info["id"], "ip": ip, "ok": False, "error": "设备认证失败"}
        resp = _get_sync_client().get(
            f"http://{ip}/ap",
            params={"a": "apadd", "ssid": ssid, "pwd": pwd},
            auth=httpx.DigestAuth(user, pw),
            timeout=TIMEOUT + 5,
        )
        return {"id": device_info["id"], "ip": ip, "ok": resp.status_code == 200}
    except HTTPException as exc:
        return {"id": device_info["id"], "ip": ip, "ok": False, "error": exc.detail}
    except Exception as exc:
        logger.warning("wifi config %s failed: %s", ip, exc)
        return {"id": device_info["id"], "ip": ip, "ok": False, "error": "WiFi配置失败"}


def _device_conn_info(device: Device) -> Dict[str, Any]:
    return {
        "id": device.id,
        "ip": device.ip,
        "alias": device.alias or "",
        "grp": device.grp or "auto",
        "user": (device.user or DEFAULTUSER).strip(),
        "pw":   (device.passwd or DEFAULTPASS).strip(),
    }


# FIX(N7): real preview that actually fetches the current WiFi from each
# device (was a fake hardcoded "(待获取)" value that the frontend then forced
# operators to click through).
@app.post("/api/devices/batch/wifi/preview")
def api_batch_wifi_preview(req: BatchWifiReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    if not infos:
        return {"results": [], "preview": True}

    def _preview(info: Dict[str, Any]) -> Dict[str, Any]:
        current = ""
        try:
            _ensure_device_ip_allowed(info["ip"])
            wifi = get_wifi_info(info["ip"], info["user"], info["pw"])
            current = wifi.get("wifiName", "")
        except HTTPException:
            current = ""
        except Exception:
            current = ""
        return {
            "id": info["id"],
            "ip": info["ip"],
            "alias": info["alias"],
            "grp":   info["grp"],
            "current_wifi": current or "(未知)",
            "new_wifi": req.ssid,
            "status": "preview",
        }

    executor = _get_shared_executor()
    results = list(executor.map(_preview, infos))
    return {"results": results, "preview": True}


@app.post("/api/devices/batch/wifi")
def api_batch_wifi(req: BatchWifiReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    if not infos:
        return {"results": []}
    executor = _get_shared_executor()
    results = list(executor.map(lambda info: wifi_task_sync(info, req.ssid, req.pwd), infos))
    return {"results": results}


@app.post("/api/devices/{devid}/sim")
def api_set_sim(devid: int, req: SimReq, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == devid).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    ip   = device.ip
    _ensure_device_ip_allowed(ip)
    user = (device.user   or DEFAULTUSER).strip()
    pw   = (device.passwd or DEFAULTPASS).strip()
    try:
        resp = _get_sync_client().post(
            f"http://{ip}/mgr",
            params={"a": "updatePhnum"},
            data={"sim1Phnum": req.sim1, "sim2Phnum": req.sim2},
            auth=httpx.DigestAuth(user, pw),
            timeout=TIMEOUT + 5,
        )
        if resp.status_code == 200:
            device.sim1number = req.sim1
            device.sim2number = req.sim2
            db.commit()
            return {"ok": True}
        return {"ok": False, "status": resp.status_code}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("set sim error device=%s: %s", devid, exc, exc_info=True)
        return {"ok": False, "error": "SIM配置失败，请稍后重试"}


def sim_task_sync(device_info: Dict[str, Any], sim1: str, sim2: str) -> Dict[str, Any]:
    ip   = device_info["ip"]
    user = device_info["user"]
    pw   = device_info["pw"]
    try:
        _ensure_device_ip_allowed(ip)
        resp = _get_sync_client().post(
            f"http://{ip}/mgr",
            params={"a": "updatePhnum"},
            data={"sim1Phnum": sim1, "sim2Phnum": sim2},
            auth=httpx.DigestAuth(user, pw),
            timeout=TIMEOUT + 5,
        )
        return {"id": device_info["id"], "ip": ip, "ok": resp.status_code == 200}
    except HTTPException as exc:
        return {"id": device_info["id"], "ip": ip, "ok": False, "error": exc.detail}
    except Exception as exc:
        logger.warning("sim config %s failed: %s", ip, exc)
        return {"id": device_info["id"], "ip": ip, "ok": False, "error": "SIM配置失败"}


@app.post("/api/devices/batch/sim")
def api_batch_sim(req: BatchSimReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    if not infos:
        return {"results": []}
    executor = _get_shared_executor()
    results = list(executor.map(lambda info: sim_task_sync(info, req.sim1, req.sim2), infos))
    for r in results:
        if r.get("ok"):
            dev = db.query(Device).filter(Device.id == r["id"]).first()
            if dev:
                dev.sim1number = req.sim1
                dev.sim2number = req.sim2
    db.commit()
    return {"results": results}


def enhanced_forward_task_sync(device_info: Dict[str, Any], req: EnhancedBatchForwardReq) -> Dict[str, Any]:
    ip   = device_info["ip"]
    user = device_info["user"]
    pw   = device_info["pw"]
    try:
        _ensure_device_ip_allowed(ip)
        ok, _ = istargetdevice(ip, user, pw)
        if not ok:
            return {"id": device_info["id"], "ip": ip, "ok": False, "error": "设备认证失败"}
        form: Dict[str, str] = {"method": req.forward_method}
        method = req.forward_method
        if method == "0":
            pass
        elif method in ("1", "2"):
            form.update(BARK_DEVICE_KEY0=req.deviceKey0, BARK_DEVICE_KEY1=req.deviceKey1, BARK_DEVICE_KEY2=req.deviceKey2)
        elif method == "8":
            form.update(SMTP_PROVIDER=req.smtpProvider, SMTP_SERVER=req.smtpServer, SMTP_PORT=req.smtpPort,
                        SMTP_ACCOUNT=req.smtpAccount, SMTP_PASSWORD=req.smtpPassword,
                        SMTP_FROM_EMAIL=req.smtpFromEmail, SMTP_TO_EMAIL=req.smtpToEmail, SMTP_ENCRYPTION=req.smtpEncryption)
        elif method in ("10", "11", "16"):
            form.update(WDF_CWH_URL1=req.webhookUrl1, WDF_CWH_URL2=req.webhookUrl2, WDF_CWH_URL3=req.webhookUrl3)
        elif method == "13":
            form.update(WDF_CWH_URL1=req.webhookUrl1, WDF_CWH_URL2=req.webhookUrl2, WDF_CWH_URL3=req.webhookUrl3,
                        WDF_SIGN_KEY1=req.signKey1, WDF_SIGN_KEY2=req.signKey2, WDF_SIGN_KEY3=req.signKey3)
        elif method == "21":
            form.update(SCT_SEND_KEY=req.sctSendKey)
        elif method == "22":
            form.update(SC3_URL=req.sc3ApiUrl)
        elif method == "30":
            form.update(PPToken=req.PPToken, PPChannel=req.PPChannel, PPWebhook=req.PPWebhook, PPFriends=req.PPFriends, PPGroupId=req.PPGroupId)
        elif method == "35":
            form.update(WPappToken=req.WPappToken, WPUID=req.WPUID, WPTopicId=req.WPTopicId)
        elif method == "90":
            form.update(LYWEB_API_URL=req.lyApiUrl)
        else:
            form.update(forwardUrl=req.forwardUrl, notifyUrl=req.notifyUrl)
        resp = _get_sync_client().post(
            f"http://{ip}/saveForwardConfig",
            data=form,
            auth=httpx.DigestAuth(user, pw),
            timeout=TIMEOUT + 5,
        )
        return {"id": device_info["id"], "ip": ip, "ok": resp.status_code == 200, "status": resp.status_code}
    except HTTPException as exc:
        return {"id": device_info["id"], "ip": ip, "ok": False, "error": exc.detail}
    except Exception as exc:
        logger.warning("forward config %s failed: %s", ip, exc)
        return {"id": device_info["id"], "ip": ip, "ok": False, "error": "转发配置失败"}


@app.post("/api/devices/batch/enhanced-forward")
def api_enhanced_batch_forward(req: EnhancedBatchForwardReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    executor = _get_shared_executor()
    results = list(executor.map(lambda info: enhanced_forward_task_sync(info, req), infos))
    return {"results": results}


@app.post("/api/devices/batch/forward")
def api_batch_forward(req: BatchForwardReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    # FIX(P1#17): reuse enhanced backend with a named constant instead of the
    # bare "99" magic string.
    fake = EnhancedBatchForwardReq(
        device_ids=req.device_ids,
        forward_method=FORWARD_METHOD_BASIC,
        forwardUrl=req.forwardUrl,
        notifyUrl=req.notifyUrl,
    )
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    executor = _get_shared_executor()
    results = list(executor.map(lambda info: enhanced_forward_task_sync(info, fake), infos))
    return {"results": results}


def _apply_regex(config: str, pattern: str, replacement: str, flags_str: str) -> Optional[str]:
    try:
        flags = 0
        for f in flags_str.lower():
            if f == "i":
                flags |= re.IGNORECASE
            elif f == "m":
                flags |= re.MULTILINE
            elif f == "s":
                flags |= re.DOTALL
            elif f.strip():
                return None
        return re.sub(pattern, replacement, config, flags=flags)
    except re.error:
        return None


def _config_main_json(content: str) -> Optional[Dict[str, Any]]:
    main_part = (content or "").split("~~--==~~--==", 1)[0].strip()
    if not main_part:
        return None
    try:
        parsed = json.loads(main_part)
    except Exception:
        return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    return parsed


def _validate_config_content(original: str, replaced: str) -> Optional[str]:
    original_main = _config_main_json(original)
    replaced_main = _config_main_json(replaced)
    if original_main and replaced_main is None:
        return "替换后开头主配置 JSON 无效，已阻止写入"
    if original_main and "~~--==~~--==" in original and "~~--==~~--==" not in replaced:
        return "替换后消息模板分隔符丢失，已阻止写入"
    if replaced.strip() in ("{}", ""):
        return "替换结果为空配置，已阻止写入"
    if replaced_main is not None:
        required_keys = {"wps", "uip"}
        if not required_keys.issubset(replaced_main.keys()):
            return "替换后主配置缺少关键字段，已阻止写入"
    return None


CLEAN_MESSAGE_TEMPLATES = """~~--==~~--==
502
{
  "msgtype": "text",
  "text": {
    "content": "【短信外发成功】{{LN}}对方号码：{{phNum|$jsonEscape()}}{{LN}}短信内容：{{smsBd|$jsonEscape()}}{{LN}}发出时间：{{YMDHMS}}{{LN}}{{LN}}发出设备：{{{devName|$jsonEscape()}}}{{LN}}发出卡槽：{{msIsdn}}（卡{{slot}}）{{scName|$jsonEscape()}}"
  }
}
~~--==~~--==
603
{
  "msgtype": "text",
  "text": {
    "content": "【来电提醒】{{LN}}号码：{{phNum|$jsonEscape()}}{{LN}}通话时间：{{telStartTs|$ts2hhmmss(':')}} 至 {{telEndTs|$ts2hhmmss(':')}}{{LN}}{{LN}}来自设备：{{{devName|$jsonEscape()}}}{{LN}}卡槽：{{msIsdn}}（卡{{slot}}）{{scName|$jsonEscape()}}"
  }
}
~~--==~~--==
695
{
  "msgtype": "voice",
  "voice": { "media_id": "{{telMediaId}}" }
}
~~--==~~--==
501
{
  "msgtype": "text",
  "text": {
    "content": "{{smsBd|$jsonEscape()}}{{LN}}短信号码：{{phNum|$jsonEscape()}}{{LN}}短信时间：{{smsTs|$ts2yyyymmddhhmmss('-',':')}}{{LN}}{{LN}}来自设备：{{{devName|$jsonEscape()}}}{{LN}}卡槽：{{msIsdn}}（卡{{slot}}）{{scName|$jsonEscape()}}"
  }
}
~~--==~~--==
209
{
  "msgtype": "text",
  "text": {
    "content": "卡{{slot}}存在故障，请将卡放入手机检查原因！{{LN}}{{LN}}SIM卡信息：{{LN}}ICCID：{{iccId}}{{LN}}IMSI：{{imsi}}{{LN}}卡号：{{msIsdn}} {{scName|$jsonEscape()}}{{LN}}{{LN}}来自设备：{{{devName|$jsonEscape()}}}{{LN}}卡槽：卡{{slot}}"
  }
}
~~--==~~--==
205
{
  "msgtype": "text",
  "text": {
    "content": "卡{{slot}}已从设备中取出！{{LN}}{{LN}}SIM卡信息：{{LN}}ICCID：{{iccId}}{{LN}}IMSI：{{imsi}}{{LN}}卡号：{{msIsdn}} {{scName|$jsonEscape()}}{{LN}}{{LN}}来自设备：{{{devName|$jsonEscape()}}}{{LN}}卡槽：卡{{slot}}"
  }
}
~~--==~~--==
204
{
  "msgtype": "text",
  "text": {
    "content": "卡{{slot}}已就绪！{{LN}}{{LN}}SIM卡信息：{{LN}}ICCID：{{iccId}}{{LN}}IMSI：{{imsi}}{{LN}}卡号：{{msIsdn}} {{scName|$jsonEscape()}}{{LN}}信号强度：{{dbm}}%{{LN}}{{LN}}来自设备：{{{devName|$jsonEscape()}}}{{LN}}卡槽：卡{{slot}}"
  }
}
~~--==~~--==
102
{
  "msgtype": "text",
  "text": {
    "content": "【设备上线提醒】{{LN}}设备已通过 卡2 上线！{{LN}}{{LN}}SIM卡信息：{{LN}}ICCID：{{iccId}}{{LN}}IMSI：{{imsi}}{{LN}}卡号：{{msIsdn}} {{scName|$jsonEscape()}}{{LN}}信号强度：{{dbm}}%{{LN}}{{LN}}来自设备：{{{devName|$jsonEscape()}}}{{LN}}卡槽：卡{{slot}}"
  }
}
~~--==~~--==
101
{
  "msgtype": "text",
  "text": {
    "content": "【设备上线提醒】{{LN}}设备已通过 卡1 上线！{{LN}}{{LN}}SIM卡信息：{{LN}}ICCID：{{iccId}}{{LN}}IMSI：{{imsi}}{{LN}}卡号：{{msIsdn}} {{scName|$jsonEscape()}}{{LN}}信号强度：{{dbm}}%{{LN}}{{LN}}来自设备：{{{devName|$jsonEscape()}}}{{LN}}卡槽：卡{{slot}}"
  }
}
~~--==~~--==
100
{
  "msgtype": "text",
  "text": {
    "content": "【设备上线提醒】{{LN}}设备已通过 WiFi 上线！{{LN}}{{LN}}本机IP：{{ip}}{{LN}}WiFi热点：{{ssid|$jsonEscape()}}{{LN}}信号强度：{{dbm}}%{{LN}}{{LN}}来自设备：{{{devName|$jsonEscape()}}}"
  }
}"""


def _apply_clean_message_template(config: str) -> Optional[str]:
    main = (config or "").split("~~--==~~--==", 1)[0].rstrip()
    if _config_main_json(main) is None:
        return None
    return f"{main}\n\n{CLEAN_MESSAGE_TEMPLATES}"


def config_read_task_sync(device_info: Dict[str, Any]) -> Dict[str, Any]:
    ip   = device_info["ip"]
    user = device_info["user"]
    pw   = device_info["pw"]
    try:
        config = read_device_config(ip, user, pw)
        if config is None:
            return {"id": device_info["id"], "ip": ip, "ok": False, "error": "读取配置失败"}
        return {"id": device_info["id"], "ip": ip, "ok": True, "config": config}
    except HTTPException as exc:
        return {"id": device_info["id"], "ip": ip, "ok": False, "error": exc.detail}
    except Exception as exc:
        return {"id": device_info["id"], "ip": ip, "ok": False, "error": str(exc)}


def config_preview_task_sync(device_info: Dict[str, Any], pattern: str, replacement: str, flags_str: str) -> Dict[str, Any]:
    result = config_read_task_sync(device_info)
    if not result.get("ok"):
        return result
    config = result.get("config", "")
    replaced = _apply_regex(config, pattern, replacement, flags_str)
    if replaced is None:
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": "正则表达式或标志位无效"}
    return {
        "id": device_info["id"],
        "ip": device_info["ip"],
        "ok": True,
        "original": config,
        "replaced": replaced,
        "changed": config != replaced,
    }


def config_preset_preview_task_sync(device_info: Dict[str, Any], preset: str) -> Dict[str, Any]:
    result = config_read_task_sync(device_info)
    if not result.get("ok"):
        return result
    config = str(result.get("config", ""))
    if preset != "clean_message_templates":
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": "未知配置预设"}
    replaced = _apply_clean_message_template(config)
    if replaced is None:
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": "主配置 JSON 无效，不能应用预设"}
    return {
        "id": device_info["id"],
        "ip": device_info["ip"],
        "ok": True,
        "original": config,
        "replaced": replaced,
        "changed": config != replaced,
    }


def config_write_task_sync(device_info: Dict[str, Any], pattern: str, replacement: str, flags_str: str) -> Dict[str, Any]:
    preview = config_preview_task_sync(device_info, pattern, replacement, flags_str)
    if not preview.get("ok"):
        return preview
    if not preview.get("changed"):
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": True, "changed": False}
    replaced = str(preview.get("replaced", ""))
    original = str(preview.get("original", ""))
    validation_error = _validate_config_content(original, replaced)
    if validation_error:
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": validation_error}
    if not write_device_config(device_info["ip"], device_info["user"], device_info["pw"], replaced):
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": "写入配置失败"}
    saved = read_device_config(device_info["ip"], device_info["user"], device_info["pw"])
    if saved is None:
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": "写入后读取校验失败"}
    saved_error = _validate_config_content(original, saved)
    if saved_error:
        write_device_config(device_info["ip"], device_info["user"], device_info["pw"], original)
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": f"写入后校验失败，已尝试恢复原配置：{saved_error}"}
    _audit("config_write", detail=f"device={device_info['id']} ip={device_info['ip']}")
    return {"id": device_info["id"], "ip": device_info["ip"], "ok": True, "changed": True}


def config_preset_write_task_sync(device_info: Dict[str, Any], preset: str) -> Dict[str, Any]:
    preview = config_preset_preview_task_sync(device_info, preset)
    if not preview.get("ok"):
        return preview
    if not preview.get("changed"):
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": True, "changed": False}
    replaced = str(preview.get("replaced", ""))
    original = str(preview.get("original", ""))
    validation_error = _validate_config_content(original, replaced)
    if validation_error:
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": validation_error}
    if not write_device_config(device_info["ip"], device_info["user"], device_info["pw"], replaced):
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": "写入配置失败"}
    saved = read_device_config(device_info["ip"], device_info["user"], device_info["pw"])
    if saved is None:
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": "写入后读取校验失败"}
    saved_error = _validate_config_content(original, saved)
    if saved_error:
        write_device_config(device_info["ip"], device_info["user"], device_info["pw"], original)
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": f"写入后校验失败，已尝试恢复原配置：{saved_error}"}
    _audit("config_preset_write", detail=f"device={device_info['id']} ip={device_info['ip']} preset={preset}")
    return {"id": device_info["id"], "ip": device_info["ip"], "ok": True, "changed": True}


def _validate_config_regex(pattern: str, replacement: str) -> None:
    if not pattern:
        raise HTTPException(status_code=400, detail="正则表达式不能为空")
    if len(pattern) > 10000:
        raise HTTPException(status_code=400, detail="正则表达式过长")
    if len(replacement) > CONFIG_MAX_CHARS:
        raise HTTPException(status_code=400, detail="替换内容过长")


def _check_config_device_ids(device_ids: List[int]) -> None:
    if not device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    if len(device_ids) > OTA_BATCH_MAX:
        raise HTTPException(status_code=400, detail=f"单次批量配置不得超过 {OTA_BATCH_MAX} 台")


@app.post("/api/devices/batch/config/read")
def api_batch_config_read(req: BatchConfigReadReq, db: Session = Depends(get_db)):
    _check_config_device_ids(req.device_ids)
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    executor = _get_shared_executor()
    configs = list(executor.map(config_read_task_sync, infos))
    return {"configs": configs}


@app.post("/api/devices/batch/config/preview")
def api_batch_config_preview(req: BatchConfigPreviewReq, db: Session = Depends(get_db)):
    _validate_config_regex(req.pattern, req.replacement)
    _check_config_device_ids(req.device_ids)
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    executor = _get_shared_executor()
    previews = list(executor.map(lambda info: config_preview_task_sync(info, req.pattern, req.replacement, req.flags), infos))
    return {"previews": previews}


@app.post("/api/devices/batch/config/preset/preview")
def api_batch_config_preset_preview(req: BatchConfigPresetReq, db: Session = Depends(get_db)):
    _check_config_device_ids(req.device_ids)
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    executor = _get_shared_executor()
    previews = list(executor.map(lambda info: config_preset_preview_task_sync(info, req.preset), infos))
    return {"previews": previews}


@app.post("/api/devices/batch/config/write")
def api_batch_config_write(req: BatchConfigWriteReq, db: Session = Depends(get_db)):
    _validate_config_regex(req.pattern, req.replacement)
    _check_config_device_ids(req.device_ids)
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    _audit("batch_config_write", detail=f"count={len(infos)} pattern_len={len(req.pattern)}")
    executor = _get_shared_executor()
    results = list(executor.map(lambda info: config_write_task_sync(info, req.pattern, req.replacement, req.flags), infos))
    return {"results": results}


@app.post("/api/devices/batch/config/preset/write")
def api_batch_config_preset_write(req: BatchConfigPresetReq, db: Session = Depends(get_db)):
    _check_config_device_ids(req.device_ids)
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    _audit("batch_config_preset_write", detail=f"count={len(infos)} preset={req.preset}")
    executor = _get_shared_executor()
    results = list(executor.map(lambda info: config_preset_write_task_sync(info, req.preset), infos))
    return {"results": results}


def _get_timeout_default() -> int:
    return int(TIMEOUT)


def fetch_device_token(ip: str, user: str, pw: str) -> str:
    _ensure_device_ip_allowed(ip)
    body = b"keys=%7B%22keys%22%3A%5B%22TOKEN%22%5D%7D"
    resp = _get_sync_client().post(
        f"http://{ip}/mgr",
        params={"a": "getHtmlData_passwdMgr"},
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        auth=httpx.DigestAuth(user, pw),
        timeout=_get_timeout_default() + 5,
    )
    resp.raise_for_status()
    payload = resp.json()
    token = (payload.get("data", {}) or {}).get("TOKEN", "") or ""
    return re.sub(r"<[^>]+>", "", str(token)).strip()


def ensure_device_token(db: Session, device: Device) -> str:
    token = (getattr(device, "token", "") or "").strip()
    if token:
        return token
    user = (getattr(device, "user",   "") or DEFAULTUSER).strip()
    pw   = (getattr(device, "passwd", "") or DEFAULTPASS).strip()
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


@app.post("/api/tel/dial")
def tel_dial(req: DirectDialReq, db: Session = Depends(get_db)):
    if req.slot not in (1, 2):
        raise HTTPException(status_code=400, detail="slot must be 1 or 2")
    phone  = _validate_phone(req.phone)
    rl_key = f"dial:{req.deviceId}"
    if not _dial_limiter.allow(rl_key):
        raise HTTPException(status_code=429, detail="拨号过于频繁，请稍后再试")
    device = db.query(Device).filter(Device.id == req.deviceId).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    _ensure_device_ip_allowed(device.ip)
    token   = ensure_device_token(db, device)
    timeout = _get_timeout_default()
    params  = {
        "token": token, "cmd": "teldial",
        "p1": str(req.slot), "p2": phone,
        "p3": str(max(10, int(req.duration or 175))),
        "p4": (req.tts or "").strip(),
        "p5": str(max(0, int(req.tts_times or 0))),
        "p6": str(max(0, int(req.tts_pause or 0))),
        "p7": str(int(req.after_action or 0)),
    }
    try:
        resp = _get_sync_client().get(f"http://{device.ip}/ctrl", params=params, timeout=timeout + 8)
        try:
            payload = resp.json()
        except Exception:
            payload = {"raw": resp.text}
        if resp.status_code == 200 and isinstance(payload, dict) and payload.get("code", 0) == 0:
            _audit("tel_dial", detail=f"device={req.deviceId} slot={req.slot} phone={phone[:4]}***")
            return {"ok": True, "resp": payload}
        return {"ok": False, "error": "设备返回拨号失败"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("dial error device=%s: %s", req.deviceId, exc, exc_info=True)
        return {"ok": False, "error": "拨号失败，请稍后重试"}


# ── OTA 批量升级 ──────────────────────────────────────────────────────────────

class BatchOtaReq(BaseModel):
    device_ids: List[int]


def _ota_check(ip: str, user: str, pw: str) -> Dict[str, Any]:
    """Raw OTA version check used by both `check` and `upgrade` flows."""
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


# FIX(N3+N4): each worker opens its own Session (SQLAlchemy Session is not
# thread-safe); store the reported version in firmware_version instead of
# overwriting the device's stable identifier devId.
def check_ota_task(device_id: int) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            return {"id": device_id, "ok": False, "error": "设备不存在"}
        ip   = device.ip
        user = (device.user   or DEFAULTUSER).strip()
        pw   = (device.passwd or DEFAULTPASS).strip()
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
            "id":         device.id,
            "ip":         ip,
            "ok":         True,
            "hasUpdate":  bool(data.get("hasUpdate", False)) or (bool(new_ver) and new_ver != cur_ver),
            "currentVer": cur_ver,
            "newVer":     new_ver,
        }
    finally:
        db.close()


# FIX(N6): only call chkNewVer once per device. If the caller already has the
# newVer from a prior check, the check phase here can be used directly for the
# decision. We intentionally do not re-query if the cached device row already
# has a known firmware_version and the caller didn't force a recheck.
def upgrade_ota_task(device_id: int) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            return {"id": device_id, "ok": False, "error": "设备不存在"}
        ip   = device.ip
        user = (device.user   or DEFAULTUSER).strip()
        pw   = (device.passwd or DEFAULTPASS).strip()
        try:
            data    = _ota_check(ip, user, pw)
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
            return {
                "id":     device.id,
                "ip":     ip,
                "ok":     upgrade_resp.status_code == 200,
                "newVer": new_ver,
            }
        except HTTPException as exc:
            return {"id": device.id, "ip": ip, "ok": False, "error": exc.detail}
        except Exception as exc:
            return {"id": device.id, "ip": ip, "ok": False, "error": str(exc)}
    finally:
        db.close()


def _check_ota_batch_allowed(request: Request, device_ids: List[int]) -> None:
    if not device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    if len(device_ids) > OTA_BATCH_MAX:
        raise HTTPException(status_code=400, detail=f"单次 OTA 批量不得超过 {OTA_BATCH_MAX} 台")
    # FIX(N5): per-caller rate limit so OTA can't be weaponised as a reboot storm.
    key = f"ota:{_client_ip(request)}"
    if not _ota_limiter.allow(key):
        raise HTTPException(status_code=429, detail="OTA 操作过于频繁，请稍后再试")


@app.post("/api/devices/batch/ota/check")
def api_batch_ota_check(req: BatchOtaReq, request: Request, db: Session = Depends(get_db)):
    _check_ota_batch_allowed(request, req.device_ids)
    existing_ids = [row.id for row in db.query(Device.id).filter(Device.id.in_(req.device_ids)).all()]
    executor = _get_shared_executor()
    results = list(executor.map(check_ota_task, existing_ids))
    return {"results": results}


@app.post("/api/devices/batch/ota/upgrade")
def api_batch_ota_upgrade(req: BatchOtaReq, request: Request, db: Session = Depends(get_db)):
    _check_ota_batch_allowed(request, req.device_ids)
    existing_ids = [row.id for row in db.query(Device.id).filter(Device.id.in_(req.device_ids)).all()]
    executor = _get_shared_executor()
    results = list(executor.map(upgrade_ota_task, existing_ids))
    _audit("ota_upgrade", detail=f"count={len(existing_ids)} ips={[r.get('ip') for r in results]}")
    return {"results": results}
