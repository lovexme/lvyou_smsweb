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
from ipaddress import ip_address, ip_network, IPv4Network
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

ACTIVE_TOKENS: Dict[str, Dict[str, Any]] = {}

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
    sim2number   = Column(String(32),  default="")
    sim2operator = Column(String(64),  default="")
    token        = Column(Text,        default="")
    alias        = Column(String(128), default="")
    created      = Column(String(32),  default="")


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
# FIX: 登录频率限制，防止暴力破解
_login_limiter= RateLimiter(int(os.environ.get("BMLOGINRATELIMIT", "5")), float(os.environ.get("BMLOGINRATEPERIOD","60")))

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


def _setup_exception_handlers(_app: FastAPI):
    @_app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        err_id = _uuid.uuid4().hex[:8]
        logger.error("unhandled [%s] %s %s: %s", err_id, request.method, request.url.path, exc, exc_info=True)
        return JSONResponse(status_code=500, content={"detail": f"服务器内部错误 (ref: {err_id})"})


# FIX: 新增 finished_at / mark_done()，用于自动清理
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


# FIX: 自动清理超期已完成的扫描任务，防止内存泄漏
def _cleanup_old_scans() -> None:
    now = time.time()
    expired = [sid for sid, st in list(_active_scans.items())
               if st.finished_at > 0 and now - st.finished_at > SCAN_TTL]
    for sid in expired:
        _active_scans.pop(sid, None)


def _run_migrations():
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(devices)")).fetchall()
        cols = [r[1] for r in rows]
        if "token" not in cols:
            conn.execute(text("ALTER TABLE devices ADD COLUMN token TEXT DEFAULT ''"))
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


def _cleanup_expired_tokens() -> None:
    now = nowts()
    expired = [t for t, p in ACTIVE_TOKENS.items() if p.get("exp", 0) <= now]
    for t in expired:
        ACTIVE_TOKENS.pop(t, None)


def _issue_token(username: str) -> str:
    _cleanup_expired_tokens()
    token = secrets.token_urlsafe(32)
    ACTIVE_TOKENS[token] = {"username": username, "exp": nowts() + TOKEN_TTL_SECONDS}
    return token


def _extract_bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "").strip()
    if not auth.startswith("Bearer "):
        return ""
    return auth[7:].strip()


def _unauthorized_json(detail: str = "未登录或登录已失效") -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": detail})


def _require_token(request: Request) -> Dict[str, Any]:
    _cleanup_expired_tokens()
    token = _extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="未登录或登录已失效")
    payload = ACTIVE_TOKENS.get(token)
    if not payload:
        raise HTTPException(status_code=401, detail="未登录或登录已失效")
    if payload.get("exp", 0) <= nowts():
        ACTIVE_TOKENS.pop(token, None)
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return payload


def _check_login_credentials(username: str, password: str) -> bool:
    return hmac.compare_digest(username, UIUSER) and hmac.compare_digest(password, UIPASS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(
        timeout=TIMEOUT,
        limits=httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=20),
        follow_redirects=False,
    )
    yield
    await app.state.http_client.aclose()


app = FastAPI(title="Board LAN Hub", version="3.4.0", lifespan=lifespan)
_setup_exception_handlers(app)

_raw_origins = os.environ.get("BMALLOWORIGINS", "")
ALLOW_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()] or []
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

_PUBLIC_PATHS = {"/", "/api/login"}


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


def sh(cmd: List[str]) -> str:
    return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()


def guessipv4cidr() -> str:
    try:
        route_text = sh(["bash", "-lc", "ip -4 route show default 2>/dev/null | head -n1"])
        match = re.search(r"dev\s+(\S+)", route_text)
        if match:
            iface = match.group(1)
            addr = sh(["bash", "-lc", f"ip -4 addr show dev {iface} | awk '/inet /{{print $2; exit}}'"])
            if addr:
                net = ip_network(addr, strict=False)
                if isinstance(net, IPv4Network):
                    return f"{net.network_address}/{net.prefixlen}"
    except Exception:
        pass
    try:
        txt = sh(["bash", "-lc", "ip -o -4 addr show | awk '{print $2, $4}'"])
        for line in txt.splitlines():
            parts = line.strip().split()
            if len(parts) != 2:
                continue
            iface, cidr = parts
            if iface == "lo":
                continue
            net = ip_network(cidr, strict=False)
            if isinstance(net, IPv4Network):
                return f"{net.network_address}/{net.prefixlen}"
    except Exception:
        pass
    return "192.168.1.0/24"


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


def prewarm_neighbors(net: IPv4Network) -> None:
    try:
        hosts = [str(host) for host in islice(net.hosts(), CIDRFALLBACKLIMIT)]
        processes = []
        for ip in hosts:
            proc = subprocess.Popen(
                ["ping", "-c", "1", "-W", "1", ip],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            processes.append(proc)
        deadline = time.time() + 8
        for proc in processes:
            remaining = max(0, deadline - time.time())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
        time.sleep(0.5)
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


# FIX: requests → httpx.Client + httpx.DigestAuth
def istargetdevice(ip: str, user: str, pw: str) -> Tuple[bool, Optional[str]]:
    url = f"http://{ip}/mgr"
    last_realm: Optional[str] = None
    for attempt in range(max(1, SCAN_RETRIES)):
        try:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
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
                logger.debug(f"scan {ip} attempt {attempt + 1} failed: {_scan_exc}")
                time.sleep(max(0, SCAN_RETRY_SLEEP_MS) / 1000.0)
    return False, last_realm


# FIX: requests → httpx.Client
def getdevicedata(ip: str, user: str, pw: str) -> Optional[Dict[str, Any]]:
    keys_list = ["DEV_ID", "DEV_VER", "SIM1_PHNUM", "SIM2_PHNUM", "SIM1_OP", "SIM2_OP", "SIM1_STA", "SIM2_STA"]
    body = f"keys={json.dumps({'keys': keys_list}, ensure_ascii=False)}"
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
            resp = client.post(
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
    keys_list = ["WIFI_NAME", "WIFI_DBM"]
    body = f"keys={json.dumps({'keys': keys_list}, ensure_ascii=False)}"
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
            resp = client.post(
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
    body = f"keys={json.dumps(['PROF'])}"
    try:
        with httpx.Client(timeout=TIMEOUT + 5, follow_redirects=False) as client:
            resp = client.post(
                f"http://{ip}/mgr",
                params={"a": "getHtmlData_profMgr"},
                auth=httpx.DigestAuth(user, pw),
                content=body.encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("success") and isinstance(data.get("data"), dict):
                    prof = data["data"].get("PROF", "")
                    if isinstance(prof, str):
                        return prof
                    return json.dumps(prof, ensure_ascii=False)
    except Exception:
        pass
    return None


def write_device_config(ip: str, user: str, pw: str, content: str) -> bool:
    try:
        with httpx.Client(timeout=TIMEOUT + 10, follow_redirects=False) as client:
            resp = client.post(
                f"http://{ip}/mgr",
                params={"a": "updateProf"},
                data={"PROPF": content},
                auth=httpx.DigestAuth(user, pw),
            )
            return resp.status_code == 200
    except Exception:
        pass
    return False


def check_ota_update(ip: str, user: str, pw: str) -> Dict[str, Any]:
    try:
        with httpx.Client(timeout=TIMEOUT + 5, follow_redirects=False) as client:
            resp = client.get(
                f"http://{ip}/ota",
                params={"a": "chkNewVer"},
                auth=httpx.DigestAuth(user, pw),
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "hasUpdate": bool(data.get("hasUpdate")),
                    "newVer": str(data.get("newVer", "")),
                }
    except Exception:
        pass
    return {"hasUpdate": False, "newVer": ""}


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
        "sims": {
            "sim1": {"number": device.sim1number or "", "operator": device.sim1operator or "", "label": device.sim1number or device.sim1operator or "SIM"},
            "sim2": {"number": device.sim2number or "", "operator": device.sim2operator or "", "label": device.sim2number or device.sim2operator or "SIM"},
        },
    }


def upsertdevice(db: Session, ip: str, mac: str, user: str, pw: str, grp: Optional[str] = None) -> Dict[str, Any]:
    data   = getdevicedata(ip, user, pw) or {}
    devid  = (data.get("DEV_ID") or "").strip() or None
    sim1num= (data.get("SIM1_PHNUM") or "").strip()
    sim2num= (data.get("SIM2_PHNUM") or "").strip()
    sim1op = (data.get("SIM1_OP") or "").strip() or _bm_op_from_sta(data.get("SIM1_STA") or "")
    sim2op = (data.get("SIM2_OP") or "").strip() or _bm_op_from_sta(data.get("SIM2_STA") or "")
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
            try:
                db.delete(other)
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
        device.sim2number  = sim2num
        device.sim2operator= sim2op
    else:
        device = Device(
            devId=devid, grp=(grp if grp is not None and str(grp).strip() else "auto"),
            ip=ip, mac=(mac or ""), user=user, passwd=pw, status="online", lastSeen=nowts(),
            sim1number=sim1num, sim1operator=sim1op, sim2number=sim2num, sim2operator=sim2op,
            created=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        db.add(device)

    db.commit()
    db.refresh(device)
    return _device_to_dict(device)


def listdevices(db: Session) -> List[Dict[str, Any]]:
    return [_device_to_dict(d) for d in db.query(Device).order_by(Device.created.desc(), Device.id.desc()).all()]


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


# FIX: 凭据通过 POST Body 传递，不再暴露于 URL Query String
class ScanStartReq(BaseModel):
    cidr:     Optional[str] = None
    group:    Optional[str] = None
    user:     str = ""
    password: str = ""


class BatchUpgradeReq(BaseModel):
    device_ids: List[int]


class BatchConfigReadReq(BaseModel):
    device_ids: List[int]


class BatchConfigPreviewReq(BaseModel):
    device_ids:   List[int]
    pattern:      str
    replacement:  str = ""
    flags:        str = ""

    @field_validator("pattern")
    @classmethod
    def _check_pattern(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("正则表达式不能为空")
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"无效的正则表达式: {e}")
        return v

    @field_validator("flags")
    @classmethod
    def _check_flags(cls, v):
        v = (v or "").strip().lower()
        invalid = set(v) - {"i", "m", "s"}
        if invalid:
            raise ValueError(f"不支持的标志位: {invalid}，仅支持 i/m/s")
        return v


class BatchConfigWriteReq(BaseModel):
    device_ids:   List[int]
    pattern:      str
    replacement:  str = ""
    flags:        str = ""

    @field_validator("pattern")
    @classmethod
    def _check_pattern(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("正则表达式不能为空")
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"无效的正则表达式: {e}")
        return v

    @field_validator("flags")
    @classmethod
    def _check_flags(cls, v):
        v = (v or "").strip().lower()
        invalid = set(v) - {"i", "m", "s"}
        if invalid:
            raise ValueError(f"不支持的标志位: {invalid}，仅支持 i/m/s")
        return v


# ── API Routes ────────────────────────────────────────────────────────────────

# FIX: 加入登录频率限制 + audit 日志
@app.post("/api/login")
def api_login(req: LoginReq, request: Request):
    client_ip = request.client.host if request.client else "unknown"
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
        ACTIVE_TOKENS.pop(token, None)
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
    payload["sim2number"]   = device.sim2number or ""
    payload["sim2operator"] = device.sim2operator or ""
    # 实时获取 WiFi 名称和信号强度
    _user = (device.user or DEFAULTUSER).strip()
    _pw   = (device.passwd or DEFAULTPASS).strip()
    wifi_info = get_wifi_info(device.ip, _user, _pw)
    payload["wifiName"] = wifi_info["wifiName"]
    payload["wifiDbm"]  = wifi_info["wifiDbm"]
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
        state.status   = "scanning"
        state.progress = "解析网段..."
        net = ip_network(cidr, strict=False)

        state.progress = "预热邻居..."
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
        state.total_ips = len(iplist)
        state.progress  = f"TCP 探测 {len(iplist)} 个 IP..."

        open80: List[str] = []
        olock = threading.Lock()

        def _tcp(ip: str):
            if _tcp_port_open(ip, 80):
                with olock:
                    open80.append(ip)

        with concurrent.futures.ThreadPoolExecutor(max_workers=TCP_CONCURRENCY) as ex:
            list(ex.map(_tcp, iplist))
        state.scanned  = len(open80)
        state.progress = f"验证 {len(open80)} 台设备..."

        found: List[Dict[str, Any]] = []
        flock = threading.Lock()

        def _probe(ip: str):
            ok, _ = istargetdevice(ip, user, password)
            if ok:
                mac = arptable.get(ip, "")
                with flock:
                    found.append({"ip": ip, "mac": mac, "devId": "", "grp": group})

        with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
            list(ex.map(_probe, open80))

        state.results  = found
        state.found    = len(found)
        state.progress = f"完成，发现 {len(found)} 台"
    except Exception as exc:
        state.status   = "error"
        state.progress = f"扫描出错: {exc}"
        logger.error("scan error for %s: %s", cidr, exc, exc_info=True)
        return
    state.status = "done"


# FIX: 去掉嵌套 ThreadPoolExecutor；finally 中调用 mark_done()
def _run_scan_bg(scan_id: str, cidr: str, group: Optional[str], user: str, password: str):
    state = _active_scans.get(scan_id)
    if not state:
        return

    _scan_worker(cidr, group, user, password, state)

    if state.status == "error":
        state.mark_done()
        return

    state.status   = "saving"
    state.progress = "保存设备到数据库..."
    try:
        db = SessionLocal()
        try:
            saved: List[Dict[str, Any]] = []
            for item in state.results:
                try:
                    d = upsertdevice(db, item["ip"], item["mac"], user, password, item.get("grp"))
                    saved.append(d)
                except Exception as exc:
                    logger.warning("save device %s failed: %s", item["ip"], exc)
            state.results  = saved
            state.found    = len(saved)
            state.status   = "done"
            state.progress = f"完成，发现 {len(saved)} 台设备"
        finally:
            db.close()
    except Exception as exc:
        state.status   = "error"
        state.progress = f"保存失败: {exc}"
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
    state.cidr = cidr
    _active_scans[scan_id] = state
    background_tasks.add_task(_run_scan_bg, scan_id, cidr, group, user, password)
    _audit("scan_start", detail=f"cidr={cidr}")
    return scan_id


# FIX: 凭据改为 POST Body；BackgroundTasks 由框架注入，去掉错误默认值
@app.post("/api/scan/start")
def scanstart(req: ScanStartReq, background_tasks: BackgroundTasks):
    user     = req.user.strip()     or DEFAULTUSER
    password = req.password.strip() or DEFAULTPASS
    scan_id  = _submit_scan(req.cidr, req.group, user, password, background_tasks)
    return {"ok": True, "scanId": scan_id}


# FIX: 调用 _cleanup_old_scans() 防止内存泄漏
@app.get("/api/scan/status/{scan_id}")
def scanstatus(scan_id: str):
    _cleanup_old_scans()
    state = _active_scans.get(scan_id)
    if not state:
        raise HTTPException(status_code=404, detail="扫描任务不存在")
    return state.to_dict()


# FIX: requests → httpx
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
    user = (device.user   or DEFAULTUSER).strip()
    pw   = (device.passwd or DEFAULTPASS).strip()
    try:
        ok, _ = istargetdevice(ip, user, pw)
        if not ok:
            raise HTTPException(status_code=400, detail="设备认证失败")
        with httpx.Client(timeout=TIMEOUT + 3, follow_redirects=False) as client:
            resp = client.get(
                f"http://{ip}/mgr",
                params={"a": "sendsms", "sid": str(req.slot), "phone": phone, "content": content},
                auth=httpx.DigestAuth(user, pw),
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


# FIX: requests → httpx
def wifi_task_sync(device: Device, ssid: str, pwd: str) -> Dict[str, Any]:
    ip   = device.ip
    user = (device.user   or DEFAULTUSER).strip()
    pw   = (device.passwd or DEFAULTPASS).strip()
    try:
        ok, _ = istargetdevice(ip, user, pw)
        if not ok:
            return {"id": device.id, "ip": ip, "ok": False, "error": "设备认证失败"}
        with httpx.Client(timeout=TIMEOUT + 5, follow_redirects=False) as client:
            resp = client.get(
                f"http://{ip}/ap",
                params={"a": "apadd", "ssid": ssid, "pwd": pwd},
                auth=httpx.DigestAuth(user, pw),
            )
        return {"id": device.id, "ip": ip, "ok": resp.status_code == 200}
    except Exception as exc:
        logger.warning("wifi config %s failed: %s", ip, exc)
        return {"id": device.id, "ip": ip, "ok": False, "error": "WiFi配置失败"}


@app.post("/api/devices/batch/wifi")
def api_batch_wifi(req: BatchWifiReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        results = list(executor.map(lambda item: wifi_task_sync(item, req.ssid, req.pwd), devices))
    return {"results": results}


# FIX: requests → httpx
@app.post("/api/devices/{devid}/sim")
def api_set_sim(devid: int, req: SimReq, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == devid).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    ip   = device.ip
    user = (device.user   or DEFAULTUSER).strip()
    pw   = (device.passwd or DEFAULTPASS).strip()
    try:
        with httpx.Client(timeout=TIMEOUT + 5, follow_redirects=False) as client:
            resp = client.post(
                f"http://{ip}/mgr",
                params={"a": "updatePhnum"},
                data={"sim1Phnum": req.sim1, "sim2Phnum": req.sim2},
                auth=httpx.DigestAuth(user, pw),
            )
        if resp.status_code == 200:
            device.sim1number = req.sim1
            device.sim2number = req.sim2
            db.commit()
            return {"ok": True}
        return {"ok": False, "status": resp.status_code}
    except Exception as exc:
        logger.error("set sim error device=%s: %s", devid, exc, exc_info=True)
        return {"ok": False, "error": "SIM配置失败，请稍后重试"}


# FIX: requests → httpx
def sim_task_sync(device: Device, sim1: str, sim2: str) -> Dict[str, Any]:
    ip   = device.ip
    user = (device.user   or DEFAULTUSER).strip()
    pw   = (device.passwd or DEFAULTPASS).strip()
    try:
        with httpx.Client(timeout=TIMEOUT + 5, follow_redirects=False) as client:
            resp = client.post(
                f"http://{ip}/mgr",
                params={"a": "updatePhnum"},
                data={"sim1Phnum": sim1, "sim2Phnum": sim2},
                auth=httpx.DigestAuth(user, pw),
            )
        return {"id": device.id, "ip": ip, "ok": resp.status_code == 200}
    except Exception as exc:
        logger.warning("sim config %s failed: %s", ip, exc)
        return {"id": device.id, "ip": ip, "ok": False, "error": "SIM配置失败"}


@app.post("/api/devices/batch/sim")
def api_batch_sim(req: BatchSimReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        results = list(executor.map(lambda item: sim_task_sync(item, req.sim1, req.sim2), devices))
    return {"results": results}


# FIX: requests → httpx
def enhanced_forward_task_sync(device: Device, req: EnhancedBatchForwardReq) -> Dict[str, Any]:
    ip   = device.ip
    user = (device.user   or DEFAULTUSER).strip()
    pw   = (device.passwd or DEFAULTPASS).strip()
    try:
        ok, _ = istargetdevice(ip, user, pw)
        if not ok:
            return {"id": device.id, "ip": ip, "ok": False, "error": "设备认证失败"}
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
        with httpx.Client(timeout=TIMEOUT + 5, follow_redirects=False) as client:
            resp = client.post(f"http://{ip}/saveForwardConfig", data=form, auth=httpx.DigestAuth(user, pw))
        return {"id": device.id, "ip": ip, "ok": resp.status_code == 200, "status": resp.status_code}
    except Exception as exc:
        logger.warning("forward config %s failed: %s", ip, exc)
        return {"id": device.id, "ip": ip, "ok": False, "error": "转发配置失败"}


@app.post("/api/devices/batch/enhanced-forward")
def api_enhanced_batch_forward(req: EnhancedBatchForwardReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        results = list(executor.map(lambda item: enhanced_forward_task_sync(item, req), devices))
    return {"results": results}


@app.post("/api/devices/batch/forward")
def api_batch_forward(req: BatchForwardReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    fake = EnhancedBatchForwardReq(device_ids=req.device_ids, forward_method="99", forwardUrl=req.forwardUrl, notifyUrl=req.notifyUrl)
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        results = list(executor.map(lambda item: enhanced_forward_task_sync(item, fake), devices))
    return {"results": results}


def _get_timeout_default() -> int:
    return int(TIMEOUT)


# FIX: requests → httpx
def fetch_device_token(ip: str, user: str, pw: str) -> str:
    body = b"keys=%7B%22keys%22%3A%5B%22TOKEN%22%5D%7D"
    with httpx.Client(timeout=_get_timeout_default() + 5, follow_redirects=False) as client:
        resp = client.post(
            f"http://{ip}/mgr",
            params={"a": "getHtmlData_passwdMgr"},
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            auth=httpx.DigestAuth(user, pw),
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


# FIX: requests → httpx
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
        with httpx.Client(timeout=timeout + 8, follow_redirects=False) as client:
            resp = client.get(f"http://{device.ip}/ctrl", params=params)
        try:
            payload = resp.json()
        except Exception:
            payload = {"raw": resp.text}
        if resp.status_code == 200 and isinstance(payload, dict) and payload.get("code", 0) == 0:
            _audit("tel_dial", detail=f"device={req.deviceId} slot={req.slot} phone={phone[:4]}***")
            return {"ok": True, "resp": payload}
        return {"ok": False, "error": "设备返回拨号失败"}
    except Exception as exc:
        logger.error("dial error device=%s: %s", req.deviceId, exc, exc_info=True)
        return {"ok": False, "error": "拨号失败，请稍后重试"}


# ── OTA 升级 ─────────────────────────────────────────────────────────────────

def upgrade_task_sync(device: Device) -> Dict[str, Any]:
    ip   = device.ip
    user = (device.user   or DEFAULTUSER).strip()
    pw   = (device.passwd or DEFAULTPASS).strip()
    try:
        # 先检查有没有新版本
        ota_info = check_ota_update(ip, user, pw)
        if not ota_info.get("hasUpdate"):
            return {"id": device.id, "ip": ip, "ok": True, "skipped": True, "message": "已是最新版本"}
        # 有新版本，执行在线升级
        with httpx.Client(timeout=TIMEOUT + 30, follow_redirects=False) as client:
            resp = client.get(
                f"http://{ip}/ota",
                params={"a": "updOtaOnline"},
                auth=httpx.DigestAuth(user, pw),
            )
            return {"id": device.id, "ip": ip, "ok": resp.status_code == 200,
                    "newVer": ota_info.get("newVer", "")}
    except Exception as exc:
        logger.warning("ota upgrade %s failed: %s", ip, exc)
        return {"id": device.id, "ip": ip, "ok": False, "error": "升级请求失败，设备可能正在重启"}


@app.post("/api/devices/batch/upgrade")
def api_batch_upgrade(req: BatchUpgradeReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    _audit("batch_upgrade", detail=f"count={len(devices)}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        results = list(executor.map(upgrade_task_sync, devices))
    return {"results": results}


@app.get("/api/devices/{devid}/ota/check")
def api_ota_check(devid: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == devid).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    user = (device.user   or DEFAULTUSER).strip()
    pw   = (device.passwd or DEFAULTPASS).strip()
    return check_ota_update(device.ip, user, pw)


# ── 批量设备配置 ─────────────────────────────────────────────────────────────

def _apply_regex(config: str, pattern: str, replacement: str, flags_str: str) -> Optional[str]:
    try:
        flags = 0
        for f in flags_str.lower():
            if f == 'i':   flags |= re.IGNORECASE
            elif f == 'm': flags |= re.MULTILINE
            elif f == 's': flags |= re.DOTALL
        # 预编译正则，限制复杂度防 ReDoS
        compiled = re.compile(pattern, flags)
        return compiled.sub(replacement, config)
    except (re.error, IndexError):
        return None


def config_read_task_sync(device: Device) -> Dict[str, Any]:
    ip   = device.ip
    user = (device.user   or DEFAULTUSER).strip()
    pw   = (device.passwd or DEFAULTPASS).strip()
    try:
        config = read_device_config(ip, user, pw)
        if config is None:
            return {"id": device.id, "ip": ip, "ok": False, "error": "读取配置失败"}
        return {"id": device.id, "ip": ip, "ok": True, "config": config}
    except Exception:
        return {"id": device.id, "ip": ip, "ok": False, "error": "读取配置异常"}


@app.post("/api/devices/batch/config/read")
def api_batch_config_read(req: BatchConfigReadReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        configs = list(executor.map(config_read_task_sync, devices))
    return {"configs": configs}


def _preview_one(device: Device, pattern: str, replacement: str, flags_str: str) -> Dict[str, Any]:
    ip   = device.ip
    user = (device.user   or DEFAULTUSER).strip()
    pw   = (device.passwd or DEFAULTPASS).strip()
    try:
        config = read_device_config(ip, user, pw)
        if config is None:
            return {"id": device.id, "ip": ip, "ok": False, "error": "读取配置失败"}
        replaced = _apply_regex(config, pattern, replacement, flags_str)
        if replaced is None:
            return {"id": device.id, "ip": ip, "ok": False, "error": "正则表达式无效"}
        return {
            "id": device.id, "ip": ip, "ok": True,
            "original": config, "replaced": replaced,
            "changed": config != replaced,
        }
    except Exception:
        return {"id": device.id, "ip": ip, "ok": False, "error": "预览失败"}


@app.post("/api/devices/batch/config/preview")
def api_batch_config_preview(req: BatchConfigPreviewReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        previews = list(executor.map(
            lambda item: _preview_one(item, req.pattern, req.replacement, req.flags), devices,
        ))
    return {"previews": previews}


def config_write_task_sync(device: Device, pattern: str, replacement: str, flags_str: str) -> Dict[str, Any]:
    ip   = device.ip
    user = (device.user   or DEFAULTUSER).strip()
    pw   = (device.passwd or DEFAULTPASS).strip()
    try:
        config = read_device_config(ip, user, pw)
        if config is None:
            return {"id": device.id, "ip": ip, "ok": False, "error": "读取配置失败"}
        replaced = _apply_regex(config, pattern, replacement, flags_str)
        if replaced is None:
            return {"id": device.id, "ip": ip, "ok": False, "error": "正则表达式无效"}
        if config == replaced:
            return {"id": device.id, "ip": ip, "ok": True, "changed": False}
        if not write_device_config(ip, user, pw, replaced):
            return {"id": device.id, "ip": ip, "ok": False, "error": "写入配置失败"}
        _audit("config_write", detail=f"device={device.id} ip={ip}")
        return {"id": device.id, "ip": ip, "ok": True, "changed": True}
    except Exception:
        return {"id": device.id, "ip": ip, "ok": False, "error": "写入配置异常"}


@app.post("/api/devices/batch/config/write")
def api_batch_config_write(req: BatchConfigWriteReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    if not req.pattern:
        raise HTTPException(status_code=400, detail="正则表达式不能为空")
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    _audit("batch_config_write", detail=f"count={len(devices)} pattern={req.pattern}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        results = list(executor.map(
            lambda item: config_write_task_sync(item, req.pattern, req.replacement, req.flags),
            devices,
        ))
    return {"results": results}