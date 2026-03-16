import asyncio
import threading
import json
import os
import re
import secrets
import sqlite3
import subprocess
import time
from ipaddress import ip_address, ip_network, IPv4Network
from typing import Any, Dict, List, Optional, Tuple
from itertools import islice
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text, BigInteger, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

import requests
import concurrent.futures
import hmac

DBPATH = os.environ.get("BMDB", "/opt/board-manager/data/data.db")
STATICDIR = os.environ.get("BMSTATIC", "/opt/board-manager/static")
DEFAULTUSER = os.environ.get("BMDEVUSER", "admin")
DEFAULTPASS = os.environ.get("BMDEVPASS", "admin")

TIMEOUT = float(os.environ.get("BMHTTPTIMEOUT", "5.0"))
CONCURRENCY = int(os.environ.get("BMSCANCONCURRENCY", "32"))
CIDRFALLBACKLIMIT = int(os.environ.get("BMCIDRFALLBACKLIMIT", "1024"))
SCAN_RETRIES = int(os.environ.get("BMSCANRETRIES", "3"))
SCAN_RETRY_SLEEP_MS = int(os.environ.get("BMSCANRETRYSLEEPMS", "300"))

UIUSER = os.environ.get("BMUIUSER", "admin")
UIPASS = os.environ.get("BMUIPASS", "admin")
TOKEN_TTL_SECONDS = int(os.environ.get("BMTOKENTTL", str(8 * 60 * 60)))

ACTIVE_TOKENS: Dict[str, Dict[str, Any]] = {}

Base = declarative_base()
engine = create_engine(
    f"sqlite:///{DBPATH}",
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True, index=True)
    devId = Column(String(128), unique=True, nullable=True)
    grp = Column(String(64), default="auto")
    ip = Column(String(45), unique=True, index=True, nullable=False)
    mac = Column(String(32), unique=True, nullable=True, default="")
    user = Column(String(64), default="")
    passwd = Column(String(64), default="")
    status = Column(String(32), default="unknown")
    lastSeen = Column(BigInteger, default=0)
    sim1number = Column(String(32), default="")
    sim1operator = Column(String(64), default="")
    sim2number = Column(String(32), default="")
    sim2operator = Column(String(64), default="")
    token = Column(Text, default="")
    alias = Column(String(128), default="")
    created = Column(String(32), default="")


Base.metadata.create_all(bind=engine)


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
    expired = [token for token, payload in ACTIVE_TOKENS.items() if payload.get("exp", 0) <= now]
    for token in expired:
        ACTIVE_TOKENS.pop(token, None)


def _issue_token(username: str) -> str:
    _cleanup_expired_tokens()
    token = secrets.token_urlsafe(32)
    ACTIVE_TOKENS[token] = {
        "username": username,
        "exp": nowts() + TOKEN_TTL_SECONDS,
    }
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
    ok_user = hmac.compare_digest(username, UIUSER)
    ok_pass = hmac.compare_digest(password, UIPASS)
    return ok_user and ok_pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(
        timeout=TIMEOUT,
        limits=httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=20),
        follow_redirects=False,
    )
    yield
    await app.state.http_client.aclose()


app = FastAPI(title="Board LAN Hub", version="3.3.0", lifespan=lifespan)

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

    if path.startswith("/static/"):
        return await call_next(request)

    if path in _PUBLIC_PATHS:
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
            addr = sh(
                ["bash", "-lc", f"ip -4 addr show dev {iface} | awk '/inet /{{print $2; exit}}'"]
            )
            if addr:
                net = ip_network(addr, strict=False)
                if isinstance(net, IPv4Network):
                    return f"{net.network_address}/{net.prefixlen}"
    except Exception:
        pass

    try:
        txt = sh(["bash", "-lc", "ip -o -4 addr show | awk '{print \(2, \)4}'"])
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
                    ip = parts[0].strip()
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
                ip = parts[0].strip()
                mac = parts[parts.index("lladdr") + 1].strip().upper()
                if mac and mac != "00:00:00:00:00:00" and ":" in mac:
                    out[ip] = mac
    except Exception:
        pass

    return out


def prewarm_neighbors(net: IPv4Network) -> None:
    processes = []
    try:
        hosts = [str(host) for host in islice(net.hosts(), min(CIDRFALLBACKLIMIT, 256))]
        for chunk_start in range(0, len(hosts), 64):
            chunk = hosts[chunk_start: chunk_start + 64]
            for ip in chunk:
                proc = subprocess.Popen(
                    ["ping", "-c", "1", "-W", "1", ip],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                processes.append(proc)

        for proc in processes:
            try:
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    except Exception:
        pass


def _bm_op_from_sta(sta: str) -> str:
    return (sta or "").strip()


def istargetdevice(ip: str, user: str, pw: str) -> Tuple[bool, Optional[str]]:
    url = f"http://{ip}/mgr"
    last_realm: Optional[str] = None
    for attempt in range(max(1, SCAN_RETRIES)):
        try:
            resp = requests.get(url, timeout=TIMEOUT, allow_redirects=False)
            if resp.status_code != 401:
                raise RuntimeError(f"unexpected status {resp.status_code}")
            header = resp.headers.get("WWW-Authenticate", "")
            if "Digest" not in header:
                raise RuntimeError("digest auth missing")
            match = re.search(r'realm="([^"]+)"', header)
            realm = match.group(1) if match else None
            last_realm = realm
            if realm != "asyncesp":
                return False, realm

            resp2 = requests.get(
                url,
                timeout=TIMEOUT,
                auth=requests.auth.HTTPDigestAuth(user, pw),
            )
            if resp2.status_code == 200:
                return True, realm
            raise RuntimeError(f"auth status {resp2.status_code}")
        except Exception:
            if attempt < max(1, SCAN_RETRIES) - 1:
                time.sleep(max(0, SCAN_RETRY_SLEEP_MS) / 1000.0)
    return False, last_realm


def getdevicedata(ip: str, user: str, pw: str) -> Optional[Dict[str, Any]]:
    url = f"http://{ip}/mgr?a=getHtmlData_index"
    keys = ["DEV_ID", "DEV_VER", "SIM1_PHNUM", "SIM2_PHNUM", "SIM1_OP", "SIM2_OP", "SIM1_STA", "SIM2_STA"]
    payload = {"keys": keys}
    try:
        resp = requests.post(
            url,
            timeout=TIMEOUT,
            auth=requests.auth.HTTPDigestAuth(user, pw),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"keys": json.dumps(payload, ensure_ascii=False)},
        )
        if resp.status_code != 200:
            return None
        body = resp.json()
        if isinstance(body, dict) and body.get("success") and isinstance(body.get("data"), dict):
            return body["data"]
    except Exception:
        pass
    return None


def _device_to_dict(device: Device) -> Dict[str, Any]:
    return {
        "id": device.id,
        "devId": device.devId or "",
        "alias": device.alias or "",
        "grp": device.grp or "auto",
        "ip": device.ip,
        "mac": device.mac or "",
        "status": device.status or "unknown",
        "lastSeen": device.lastSeen or 0,
        "created": device.created or "",
        "sims": {
            "sim1": {
                "number": device.sim1number or "",
                "operator": device.sim1operator or "",
                "label": device.sim1number or device.sim1operator or "SIM",
            },
            "sim2": {
                "number": device.sim2number or "",
                "operator": device.sim2operator or "",
                "label": device.sim2number or device.sim2operator or "SIM",
            },
        },
    }


def upsertdevice(
    db: Session,
    ip: str,
    mac: str,
    user: str,
    pw: str,
    grp: Optional[str] = None,
) -> Dict[str, Any]:
    data = getdevicedata(ip, user, pw) or {}
    devid = (data.get("DEV_ID") or "").strip() or None
    sim1num = (data.get("SIM1_PHNUM") or "").strip()
    sim2num = (data.get("SIM2_PHNUM") or "").strip()
    sim1op = (data.get("SIM1_OP") or "").strip() or _bm_op_from_sta(data.get("SIM1_STA") or "")
    sim2op = (data.get("SIM2_OP") or "").strip() or _bm_op_from_sta(data.get("SIM2_STA") or "")
    mac = (mac or "").strip().upper() or None

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

    if device:
        device.devId = devid if devid else device.devId
        if grp is not None and str(grp).strip():
            device.grp = grp
        device.ip = ip
        device.mac = mac if mac else device.mac
        device.user = user
        device.passwd = pw
        device.status = "online"
        device.lastSeen = nowts()
        device.sim1number = sim1num
        device.sim1operator = sim1op
        device.sim2number = sim2num
        device.sim2operator = sim2op
    else:
        device = Device(
            devId=devid,
            grp=(grp if grp is not None and str(grp).strip() else "auto"),
            ip=ip,
            mac=(mac or ""),
            user=user,
            passwd=pw,
            status="online",
            lastSeen=nowts(),
            sim1number=sim1num,
            sim1operator=sim1op,
            sim2number=sim2num,
            sim2operator=sim2op,
            created=subprocess.check_output(["date", "+%Y-%m-%d %H:%M:%S"], text=True).strip(),
        )
        db.add(device)

    db.commit()
    db.refresh(device)
    return _device_to_dict(device)


def listdevices(db: Session) -> List[Dict[str, Any]]:
    devices = db.query(Device).order_by(Device.created.desc(), Device.id.desc()).all()
    return [_device_to_dict(device) for device in devices]


def getallnumbers(db: Session) -> List[Dict[str, Any]]:
    numbers = []
    for device in db.query(Device).all():
        for num, op, slot in [
            (device.sim1number, device.sim1operator, 1),
            (device.sim2number, device.sim2operator, 2),
        ]:
            if num and num.strip():
                numbers.append(
                    {
                        "deviceId": device.id,
                        "deviceName": device.devId or device.ip,
                        "ip": device.ip,
                        "number": num.strip(),
                        "operator": op or "",
                        "slot": slot,
                    }
                )
    return numbers


class LoginReq(BaseModel):
    username: str
    password: str


class DirectSmsReq(BaseModel):
    deviceId: int
    phone: str
    content: str
    slot: int


class DirectDialReq(BaseModel):
    deviceId: int
    slot: int
    phone: str
    tts: str = ""
    duration: int = 175
    tts_times: int = 2
    tts_pause: int = 1
    after_action: int = 1


class AliasReq(BaseModel):
    alias: str


class GroupReq(BaseModel):
    group: str


class BatchDeleteReq(BaseModel):
    device_ids: List[int]


class BatchWifiReq(BaseModel):
    device_ids: List[int]
    ssid: str
    pwd: str


class SimReq(BaseModel):
    sim1: str = ""
    sim2: str = ""


class BatchSimReq(BaseModel):
    device_ids: List[int]
    sim1: str = ""
    sim2: str = ""


class BatchForwardReq(BaseModel):
    device_ids: List[int]
    forwardUrl: str = ""
    notifyUrl: str = ""


class EnhancedBatchForwardReq(BaseModel):
    device_ids: List[int]
    forward_method: str
    forwardUrl: str = ""
    notifyUrl: str = ""
    deviceKey0: str = ""
    deviceKey1: str = ""
    deviceKey2: str = ""
    smtpProvider: str = ""
    smtpServer: str = ""
    smtpPort: str = ""
    smtpAccount: str = ""
    smtpPassword: str = ""
    smtpFromEmail: str = ""
    smtpToEmail: str = ""
    smtpEncryption: str = ""
    webhookUrl1: str = ""
    webhookUrl2: str = ""
    webhookUrl3: str = ""
    signKey1: str = ""
    signKey2: str = ""
    signKey3: str = ""
    sc3ApiUrl: str = ""
    sctSendKey: str = ""
    PPToken: str = ""
    PPChannel: str = ""
    PPWebhook: str = ""
    PPFriends: str = ""
    PPGroupId: str = ""
    WPappToken: str = ""
    WPUID: str = ""
    WPTopicId: str = ""
    lyApiUrl: str = ""


@app.post("/api/login")
def api_login(req: LoginReq):
    username = (req.username or "").strip()
    password = req.password or ""
    if not _check_login_credentials(username, password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = _issue_token(username)
    return {
        "ok": True,
        "token": token,
        "username": username,
        "expiresIn": TOKEN_TTL_SECONDS,
    }


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
def apidevices(db: Session = Depends(get_db)):
    return listdevices(db)


@app.get("/api/numbers")
def apinumbers(db: Session = Depends(get_db)):
    return getallnumbers(db)


@app.get("/api/devices/{devid}/detail")
def api_device_detail(devid: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == devid).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    payload = _device_to_dict(device)
    payload["sim1number"] = device.sim1number or ""
    payload["sim1operator"] = device.sim1operator or ""
    payload["sim2number"] = device.sim2number or ""
    payload["sim2operator"] = device.sim2operator or ""
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


@app.post("/api/scan/start")
def scanstart(
    cidr: Optional[str] = None,
    group: Optional[str] = None,
    user: str = DEFAULTUSER,
    password: str = DEFAULTPASS,
    db: Session = Depends(get_db),
):
    if not cidr:
        cidr = guessipv4cidr()
    try:
        net = ip_network(cidr, strict=False)
        if not isinstance(net, IPv4Network):
            raise ValueError("only IPv4 supported")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"bad cidr: {exc}")

    prewarm_neighbors(net)
    arptable = getarptable()

    seen: set[str] = set()
    iplist: List[str] = []
    arp_ips = [ip for ip in arptable if _safe_ip_in_net(ip, net)]
    for ip in arp_ips + [str(host) for host in islice(net.hosts(), CIDRFALLBACKLIMIT)]:
        if ip not in seen:
            seen.add(ip)
            iplist.append(ip)
        if len(iplist) >= CIDRFALLBACKLIMIT:
            break

    found: List[Dict[str, Any]] = []
    found_lock = threading.Lock()

    def probe(ip: str):
        ok, _ = istargetdevice(ip, user, password)
        if ok:
            mac = arptable.get(ip, "")
            thread_db = SessionLocal()
            try:
                device_data = upsertdevice(thread_db, ip, mac, user, password, group)
                with found_lock:
                    found.append(device_data)
            finally:
                thread_db.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        list(executor.map(probe, iplist))

    return {
        "ok": True,
        "cidr": cidr,
        "found": len(found),
        "devices": [{"ip": item["ip"], "devId": item.get("devId", "")} for item in found],
    }


@app.post("/api/sms/send-direct")
def smssenddirect(req: DirectSmsReq, db: Session = Depends(get_db)):
    if req.slot not in (1, 2):
        raise HTTPException(status_code=400, detail="slot must be 1 or 2")
    phone = req.phone.strip()
    content = req.content.strip()
    if not phone or not content:
        raise HTTPException(status_code=400, detail="phone/content required")

    device = db.query(Device).filter(Device.id == req.deviceId).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    ip = device.ip
    user = (device.user or DEFAULTUSER).strip()
    pw = (device.passwd or DEFAULTPASS).strip()

    try:
        ok, _ = istargetdevice(ip, user, pw)
        if not ok:
            raise HTTPException(status_code=400, detail="Device authentication failed")

        resp = requests.get(
            f"http://{ip}/mgr",
            params={"a": "sendsms", "sid": str(req.slot), "phone": phone, "content": content},
            timeout=TIMEOUT + 3,
            auth=requests.auth.HTTPDigestAuth(user, pw),
        )
        if resp.status_code == 200:
            try:
                body = resp.json()
                if isinstance(body, dict) and body.get("success") is True:
                    return {"ok": True}
                return {"ok": False, "error": f"device response: {body}"}
            except Exception:
                return {"ok": False, "error": "non-json response"}
        return {"ok": False, "error": f"http {resp.status_code}"}
    except HTTPException:
        raise
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def wifi_task_sync(device: Device, ssid: str, pwd: str) -> Dict[str, Any]:
    ip = device.ip
    user = (device.user or DEFAULTUSER).strip()
    pw = (device.passwd or DEFAULTPASS).strip()
    try:
        ok, _ = istargetdevice(ip, user, pw)
        if not ok:
            return {"id": device.id, "ip": ip, "ok": False, "error": "auth failed"}
        resp = requests.get(
            f"http://{ip}/ap",
            params={"a": "apadd", "ssid": ssid, "pwd": pwd},
            timeout=TIMEOUT + 5,
            auth=requests.auth.HTTPDigestAuth(user, pw),
        )
        return {"id": device.id, "ip": ip, "ok": resp.status_code == 200}
    except Exception as exc:
        return {"id": device.id, "ip": ip, "ok": False, "error": str(exc)}


@app.post("/api/devices/batch/wifi")
def api_batch_wifi(req: BatchWifiReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        results = list(executor.map(lambda item: wifi_task_sync(item, req.ssid, req.pwd), devices))
    return {"results": results}


@app.post("/api/devices/{devid}/sim")
def api_set_sim(devid: int, req: SimReq, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == devid).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    ip = device.ip
    user = (device.user or DEFAULTUSER).strip()
    pw = (device.passwd or DEFAULTPASS).strip()
    try:
        resp = requests.post(
            f"http://{ip}/mgr",
            params={"a": "updatePhnum"},
            data={"sim1Phnum": req.sim1, "sim2Phnum": req.sim2},
            timeout=TIMEOUT + 5,
            auth=requests.auth.HTTPDigestAuth(user, pw),
        )
        if resp.status_code == 200:
            device.sim1number = req.sim1
            device.sim2number = req.sim2
            db.commit()
            return {"ok": True}
        return {"ok": False, "status": resp.status_code}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def sim_task_sync(device: Device, sim1: str, sim2: str) -> Dict[str, Any]:
    ip = device.ip
    user = (device.user or DEFAULTUSER).strip()
    pw = (device.passwd or DEFAULTPASS).strip()
    try:
        resp = requests.post(
            f"http://{ip}/mgr",
            params={"a": "updatePhnum"},
            data={"sim1Phnum": sim1, "sim2Phnum": sim2},
            timeout=TIMEOUT + 5,
            auth=requests.auth.HTTPDigestAuth(user, pw),
        )
        return {"id": device.id, "ip": ip, "ok": resp.status_code == 200}
    except Exception as exc:
        return {"id": device.id, "ip": ip, "ok": False, "error": str(exc)}


@app.post("/api/devices/batch/sim")
def api_batch_sim(req: BatchSimReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        results = list(executor.map(lambda item: sim_task_sync(item, req.sim1, req.sim2), devices))
    return {"results": results}


def enhanced_forward_task_sync(device: Device, req: EnhancedBatchForwardReq) -> Dict[str, Any]:
    ip = device.ip
    user = (device.user or DEFAULTUSER).strip()
    pw = (device.passwd or DEFAULTPASS).strip()
    try:
        ok, _ = istargetdevice(ip, user, pw)
        if not ok:
            return {"id": device.id, "ip": ip, "ok": False, "error": "auth failed"}

        form: Dict[str, str] = {"method": req.forward_method}
        method = req.forward_method
        if method == "0":
            pass
        elif method in ("1", "2"):
            form.update(
                BARK_DEVICE_KEY0=req.deviceKey0,
                BARK_DEVICE_KEY1=req.deviceKey1,
                BARK_DEVICE_KEY2=req.deviceKey2,
            )
        elif method == "8":
            form.update(
                SMTP_PROVIDER=req.smtpProvider,
                SMTP_SERVER=req.smtpServer,
                SMTP_PORT=req.smtpPort,
                SMTP_ACCOUNT=req.smtpAccount,
                SMTP_PASSWORD=req.smtpPassword,
                SMTP_FROM_EMAIL=req.smtpFromEmail,
                SMTP_TO_EMAIL=req.smtpToEmail,
                SMTP_ENCRYPTION=req.smtpEncryption,
            )
        elif method in ("10", "11", "16"):
            form.update(
                WDF_CWH_URL1=req.webhookUrl1,
                WDF_CWH_URL2=req.webhookUrl2,
                WDF_CWH_URL3=req.webhookUrl3,
            )
        elif method == "13":
            form.update(
                WDF_CWH_URL1=req.webhookUrl1,
                WDF_CWH_URL2=req.webhookUrl2,
                WDF_CWH_URL3=req.webhookUrl3,
                WDF_SIGN_KEY1=req.signKey1,
                WDF_SIGN_KEY2=req.signKey2,
                WDF_SIGN_KEY3=req.signKey3,
            )
        elif method == "21":
            form.update(SCT_SEND_KEY=req.sctSendKey)
        elif method == "22":
            form.update(SC3_URL=req.sc3ApiUrl)
        elif method == "30":
            form.update(
                PPToken=req.PPToken,
                PPChannel=req.PPChannel,
                PPWebhook=req.PPWebhook,
                PPFriends=req.PPFriends,
                PPGroupId=req.PPGroupId,
            )
        elif method == "35":
            form.update(
                WPappToken=req.WPappToken,
                WPUID=req.WPUID,
                WPTopicId=req.WPTopicId,
            )
        elif method == "90":
            form.update(LYWEB_API_URL=req.lyApiUrl)
        else:
            form.update(forwardUrl=req.forwardUrl, notifyUrl=req.notifyUrl)

        resp = requests.post(
            f"http://{ip}/saveForwardConfig",
            data=form,
            timeout=TIMEOUT + 5,
            auth=requests.auth.HTTPDigestAuth(user, pw),
        )
        return {
            "id": device.id,
            "ip": ip,
            "ok": resp.status_code == 200,
            "status": resp.status_code,
        }
    except Exception as exc:
        return {"id": device.id, "ip": ip, "ok": False, "error": str(exc)}


@app.post("/api/devices/batch/enhanced-forward")
async def api_enhanced_batch_forward(req: EnhancedBatchForwardReq, db: Session = Depends(get_db)):
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
    fake = EnhancedBatchForwardReq(
        device_ids=req.device_ids,
        forward_method="99",
        forwardUrl=req.forwardUrl,
        notifyUrl=req.notifyUrl,
    )
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        results = list(executor.map(lambda item: enhanced_forward_task_sync(item, fake), devices))
    return {"results": results}


def _get_timeout_default() -> int:
    return int(TIMEOUT)


def fetch_device_token(ip: str, user: str, pw: str) -> str:
    url = f"http://{ip}/mgr?a=getHtmlData_passwdMgr"
    body = 'keys=%7B%22keys%22%3A%5B%22TOKEN%22%5D%7D'
    resp = requests.post(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=_get_timeout_default() + 5,
        auth=requests.auth.HTTPDigestAuth(user, pw),
    )
    resp.raise_for_status()
    payload = resp.json()
    token = (payload.get("data", {}) or {}).get("TOKEN", "") or ""
    return re.sub(r"<[^>]+>", "", str(token)).strip()


def ensure_device_token(db: Session, device: Device) -> str:
    token = (getattr(device, "token", "") or "").strip()
    if token:
        return token
    user = (getattr(device, "user", "") or DEFAULTUSER).strip()
    pw = (getattr(device, "passwd", "") or DEFAULTPASS).strip()
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
    phone = (req.phone or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone required")

    device = db.query(Device).filter(Device.id == req.deviceId).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    token = ensure_device_token(db, device)
    timeout = _get_timeout_default()
    params = {
        "token": token,
        "cmd": "teldial",
        "p1": str(req.slot),
        "p2": phone,
        "p3": str(max(10, int(req.duration or 175))),
        "p4": (req.tts or "").strip(),
        "p5": str(max(0, int(req.tts_times or 0))),
        "p6": str(max(0, int(req.tts_pause or 0))),
        "p7": str(int(req.after_action or 0)),
    }
    try:
        resp = requests.get(f"http://{device.ip}/ctrl", params=params, timeout=timeout + 8)
        try:
            payload = resp.json()
        except Exception:
            payload = {"raw": resp.text}
        if resp.status_code == 200 and isinstance(payload, dict) and payload.get("code", 0) == 0:
            return {"ok": True, "resp": payload}
        return {"ok": False, "error": payload}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
