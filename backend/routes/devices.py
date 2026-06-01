"""Device management routes.

Extracted from backend/main.py to keep the main module under 1000 lines.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from backend.config import BATCH_MAX, DEFAULTPASS, DEFAULTUSER, OTA_BATCH_MAX, SMS_MAX_LEN, TIMEOUT
from backend.db import Device, SessionLocal, get_db, nowts
from backend.security import is_device_ip_allowed as _is_device_ip_allowed

logger = logging.getLogger("board-manager")

router = APIRouter()


# ── Shared helpers (imported from main via a lightweight bridge) ──────────────
# These are injected at registration time to avoid circular imports.
_get_sync_client = None
_get_shared_executor = None
_ensure_device_ip_allowed = None
_ensure_device_ip_allowed_raise = None
istargetdevice = None
getdevicedata = None
get_wifi_info = None
read_device_config = None
write_device_config = None
_device_to_dict = None
_device_conn_info = None
upsertdevice = None
_audit = None
_validate_phone = None
_validate_sms_content = None
_sms_limiter = None
_dial_limiter = None
_ota_limiter = None
_client_ip = None
_check_login_credentials = None


def inject(
    *,
    get_sync_client=None,
    get_shared_executor=None,
    ensure_device_ip_allowed=None,
    ensure_device_ip_allowed_raise=None,
    is_target_device=None,
    get_device_data=None,
    get_wifi_info_fn=None,
    read_device_config_fn=None,
    write_device_config_fn=None,
    device_to_dict=None,
    device_conn_info=None,
    upsert_device=None,
    audit=None,
    validate_phone=None,
    validate_sms_content=None,
    sms_limiter=None,
    dial_limiter=None,
    ota_limiter=None,
    client_ip=None,
    check_login_credentials=None,
):
    global _get_sync_client, _get_shared_executor, _ensure_device_ip_allowed
    global _ensure_device_ip_allowed_raise, istargetdevice, getdevicedata
    global get_wifi_info, read_device_config, write_device_config
    global _device_to_dict, _device_conn_info, upsertdevice, _audit
    global _validate_phone, _validate_sms_content, _sms_limiter, _dial_limiter
    global _ota_limiter, _client_ip, _check_login_credentials
    if get_sync_client: _get_sync_client = get_sync_client
    if get_shared_executor: _get_shared_executor = get_shared_executor
    if ensure_device_ip_allowed: _ensure_device_ip_allowed = ensure_device_ip_allowed
    if ensure_device_ip_allowed_raise: _ensure_device_ip_allowed_raise = ensure_device_ip_allowed_raise
    if is_target_device: istargetdevice = is_target_device
    if get_device_data: getdevicedata = get_device_data
    if get_wifi_info_fn: get_wifi_info = get_wifi_info_fn
    if read_device_config_fn: read_device_config = read_device_config_fn
    if write_device_config_fn: write_device_config = write_device_config_fn
    if device_to_dict: _device_to_dict = device_to_dict
    if device_conn_info: _device_conn_info = device_conn_info
    if upsert_device: upsertdevice = upsert_device
    if audit: _audit = audit
    if validate_phone: _validate_phone = validate_phone
    if validate_sms_content: _validate_sms_content = validate_sms_content
    if sms_limiter: _sms_limiter = sms_limiter
    if dial_limiter: _dial_limiter = dial_limiter
    if ota_limiter: _ota_limiter = ota_limiter
    if client_ip: _client_ip = client_ip
    if check_login_credentials: _check_login_credentials = check_login_credentials


# ── Pydantic Models ──────────────────────────────────────────────────────────

class DirectSmsReq(BaseModel):
    deviceId: int
    phone: str
    content: str
    slot: int

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v):
        from backend.main import PHONE_RE
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
        from backend.main import PHONE_RE
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
    pwd: str


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
    device_ids: List[int]
    pattern: str
    replacement: str = ""
    flags: str = ""


class BatchConfigWriteReq(BaseModel):
    device_ids: List[int]
    pattern: str
    replacement: str = ""
    flags: str = ""


class BatchConfigPresetReq(BaseModel):
    device_ids: List[int]
    preset: str = "clean_message_templates"


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


class BatchOtaReq(BaseModel):
    device_ids: List[int]


# ── Device list / CRUD ───────────────────────────────────────────────────────

@router.get("/api/devices")
def apidevices(
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=1000),
    q: str = Query("", max_length=128),
    group: str = Query("", max_length=64),
    db: Session = Depends(get_db),
):
    from backend.main import _apply_devices_filter
    query = db.query(Device).order_by(Device.created.desc(), Device.id.desc())
    query = _apply_devices_filter(query, q, group)
    total = query.count()
    online = query.filter(Device.status == "online").count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [_device_to_dict(d) for d in items],
        "total": total,
        "online_count": online,
        "offline_count": max(total - online, 0),
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
    }


@router.get("/api/devices/groups")
def apidevicesgroups(db: Session = Depends(get_db)):
    rows = (
        db.query(Device.grp)
        .filter(Device.grp.isnot(None), Device.grp != "")
        .distinct()
        .all()
    )
    groups = sorted({row[0] for row in rows if row[0]})
    return {"items": groups}


@router.get("/api/numbers")
def apinumbers(
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=1000),
    q: str = Query("", max_length=128),
    group: str = Query("", max_length=64),
    db: Session = Depends(get_db),
):
    from backend.main import getallnumbers
    all_nums = getallnumbers(db, group=group)
    qval = (q or "").strip().lower()
    if qval:
        all_nums = [
            n for n in all_nums
            if qval in (n.get("number") or "").lower()
            or qval in (n.get("operator") or "").lower()
            or qval in (n.get("deviceName") or "").lower()
        ]
    total = len(all_nums)
    start = (page - 1) * page_size
    return {
        "items": all_nums[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
    }


@router.get("/api/devices/{devid}/detail")
def api_device_detail(devid: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == devid).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    payload = _device_to_dict(device)
    payload["sim1number"] = device.sim1number or ""
    payload["sim1operator"] = device.sim1operator or ""
    payload["sim1signal"] = device.sim1signal or 0
    payload["sim2number"] = device.sim2number or ""
    payload["sim2operator"] = device.sim2operator or ""
    payload["sim2signal"] = device.sim2signal or 0
    sig_data = getdevicedata(device.ip, device.user or DEFAULTUSER, device.passwd or DEFAULTPASS) or {}
    payload["wifiName"] = sig_data.get("WIFI_NAME", "")
    payload["wifiDbm"] = sig_data.get("WIFI_DBM", "")
    return {"device": payload, "forwardconfig": {}, "wifilist": []}


@router.post("/api/devices/{devid}/alias")
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


@router.post("/api/devices/{devid}/group")
def api_set_group(devid: int, req: GroupReq, db: Session = Depends(get_db)):
    group = (req.group or "").strip() or "auto"
    device = db.query(Device).filter(Device.id == devid).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    device.grp = group
    db.commit()
    return {"ok": True}


@router.delete("/api/devices/{dev_id}")
def deletedevice(dev_id: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == dev_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    db.delete(device)
    db.commit()
    return {"ok": True}


@router.post("/api/devices/batch/delete")
def api_batch_delete(req: BatchDeleteReq, db: Session = Depends(get_db)):
    if not req.device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    deleted = (
        db.query(Device)
        .filter(Device.id.in_(req.device_ids))
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "deleted": deleted}


# ── SMS / Dial ───────────────────────────────────────────────────────────────

@router.post("/api/sms/send-direct")
def smssenddirect(req: DirectSmsReq, db: Session = Depends(get_db)):
    if req.slot not in (1, 2):
        raise HTTPException(status_code=400, detail="slot must be 1 or 2")
    phone = _validate_phone(req.phone)
    content = _validate_sms_content(req.content)
    rl_key = f"sms:{req.deviceId}"
    if not _sms_limiter.allow(rl_key):
        remaining = _sms_limiter.remaining(rl_key)
        raise HTTPException(status_code=429, detail=f"发送过于频繁，请稍后再试（剩余{remaining}次/分钟）")
    device = db.query(Device).filter(Device.id == req.deviceId).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    ip = device.ip
    _ensure_device_ip_allowed_raise(ip)
    user = (device.user or DEFAULTUSER).strip()
    pw = (device.passwd or DEFAULTPASS).strip()
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


@router.post("/api/tel/dial")
def tel_dial(req: DirectDialReq, db: Session = Depends(get_db)):
    from backend.main import ensure_device_token, fetch_device_token, PHONE_RE
    if req.slot not in (1, 2):
        raise HTTPException(status_code=400, detail="slot must be 1 or 2")
    phone = _validate_phone(req.phone)
    rl_key = f"dial:{req.deviceId}"
    if not _dial_limiter.allow(rl_key):
        raise HTTPException(status_code=429, detail="拨号过于频繁，请稍后再试")
    device = db.query(Device).filter(Device.id == req.deviceId).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    _ensure_device_ip_allowed_raise(device.ip)
    token = ensure_device_token(db, device)
    timeout = int(TIMEOUT)
    params = {
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


# ── WiFi / SIM / Forward batch operations ────────────────────────────────────

def _check_batch_size(device_ids: Optional[List[int]]) -> None:
    """FIX(M2): require a non-empty device list and cap its size so a single
    wifi/sim/forward batch can't fan out unbounded outbound device traffic."""
    if not device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    if len(device_ids) > BATCH_MAX:
        raise HTTPException(status_code=400, detail=f"单次批量操作不得超过 {BATCH_MAX} 台")


def _wifi_task_sync(device_info: Dict[str, Any], ssid: str, pwd: str) -> Dict[str, Any]:
    ip = device_info["ip"]
    user = device_info["user"]
    pw = device_info["pw"]
    try:
        _ensure_device_ip_allowed_raise(ip)
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


@router.post("/api/devices/batch/wifi/preview")
def api_batch_wifi_preview(req: BatchWifiReq, db: Session = Depends(get_db)):
    _check_batch_size(req.device_ids)
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    if not infos:
        return {"results": [], "preview": True}

    def _preview(info: Dict[str, Any]) -> Dict[str, Any]:
        current = ""
        try:
            _ensure_device_ip_allowed_raise(info["ip"])
            wifi = get_wifi_info(info["ip"], info["user"], info["pw"])
            current = wifi.get("wifiName", "")
        except (HTTPException, Exception):
            current = ""
        return {
            "id": info["id"], "ip": info["ip"], "alias": info["alias"], "grp": info["grp"],
            "current_wifi": current or "(未知)", "new_wifi": req.ssid, "status": "preview",
        }

    executor = _get_shared_executor()
    results = list(executor.map(_preview, infos))
    return {"results": results, "preview": True}


@router.post("/api/devices/batch/wifi")
def api_batch_wifi(req: BatchWifiReq, db: Session = Depends(get_db)):
    _check_batch_size(req.device_ids)
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    if not infos:
        return {"results": []}
    executor = _get_shared_executor()
    results = list(executor.map(lambda info: _wifi_task_sync(info, req.ssid, req.pwd), infos))
    return {"results": results}


def _sim_task_sync(device_info: Dict[str, Any], sim1: str, sim2: str) -> Dict[str, Any]:
    ip = device_info["ip"]
    user = device_info["user"]
    pw = device_info["pw"]
    try:
        _ensure_device_ip_allowed_raise(ip)
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


@router.post("/api/devices/batch/sim")
def api_batch_sim(req: BatchSimReq, db: Session = Depends(get_db)):
    _check_batch_size(req.device_ids)
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    if not infos:
        return {"results": []}
    executor = _get_shared_executor()
    results = list(executor.map(lambda info: _sim_task_sync(info, req.sim1, req.sim2), infos))
    ok_ids = [r["id"] for r in results if r.get("ok")]
    if ok_ids:
        dev_map = {d.id: d for d in db.query(Device).filter(Device.id.in_(ok_ids)).all()}
        for dev_id in ok_ids:
            dev = dev_map.get(dev_id)
            if dev:
                dev.sim1number = req.sim1
                dev.sim2number = req.sim2
        db.commit()
    return {"results": results}


# NOTE: the single-device sim route is declared AFTER /api/devices/batch/sim so
# the literal "batch" path is matched first; otherwise the int path param
# {devid} would shadow it and 422 on "batch".
@router.post("/api/devices/{devid}/sim")
def api_set_sim(devid: int, req: SimReq, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == devid).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    ip = device.ip
    _ensure_device_ip_allowed_raise(ip)
    user = (device.user or DEFAULTUSER).strip()
    pw = (device.passwd or DEFAULTPASS).strip()
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


def _enhanced_forward_task_sync(device_info: Dict[str, Any], req: EnhancedBatchForwardReq) -> Dict[str, Any]:
    ip = device_info["ip"]
    user = device_info["user"]
    pw = device_info["pw"]
    try:
        _ensure_device_ip_allowed_raise(ip)
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
            f"http://{ip}/saveForwardConfig", data=form,
            auth=httpx.DigestAuth(user, pw), timeout=TIMEOUT + 5,
        )
        return {"id": device_info["id"], "ip": ip, "ok": resp.status_code == 200, "status": resp.status_code}
    except HTTPException as exc:
        return {"id": device_info["id"], "ip": ip, "ok": False, "error": exc.detail}
    except Exception as exc:
        logger.warning("forward config %s failed: %s", ip, exc)
        return {"id": device_info["id"], "ip": ip, "ok": False, "error": "转发配置失败"}


@router.post("/api/devices/batch/enhanced-forward")
def api_enhanced_batch_forward(req: EnhancedBatchForwardReq, db: Session = Depends(get_db)):
    _check_batch_size(req.device_ids)
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    executor = _get_shared_executor()
    results = list(executor.map(lambda info: _enhanced_forward_task_sync(info, req), infos))
    return {"results": results}


@router.post("/api/devices/batch/forward")
def api_batch_forward(req: BatchForwardReq, db: Session = Depends(get_db)):
    from backend.config import FORWARD_METHOD_BASIC
    _check_batch_size(req.device_ids)
    fake = EnhancedBatchForwardReq(
        device_ids=req.device_ids, forward_method=FORWARD_METHOD_BASIC,
        forwardUrl=req.forwardUrl, notifyUrl=req.notifyUrl,
    )
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    executor = _get_shared_executor()
    results = list(executor.map(lambda info: _enhanced_forward_task_sync(info, fake), infos))
    return {"results": results}


# ── OTA ──────────────────────────────────────────────────────────────────────

def _check_ota_batch_allowed(request, device_ids: List[int]) -> None:
    if not device_ids:
        raise HTTPException(status_code=400, detail="device_ids required")
    if len(device_ids) > OTA_BATCH_MAX:
        raise HTTPException(status_code=400, detail=f"单次 OTA 批量不得超过 {OTA_BATCH_MAX} 台")
    key = f"ota:{_client_ip(request)}"
    if not _ota_limiter.allow(key):
        raise HTTPException(status_code=429, detail="OTA 操作过于频繁，请稍后再试")


@router.post("/api/devices/batch/ota/check")
def api_batch_ota_check(req: BatchOtaReq, request: Request, db: Session = Depends(get_db)):
    from backend.main import check_ota_task
    _check_ota_batch_allowed(request, req.device_ids)
    existing_ids = [row.id for row in db.query(Device.id).filter(Device.id.in_(req.device_ids)).all()]
    executor = _get_shared_executor()
    results = list(executor.map(check_ota_task, existing_ids))
    return {"results": results}


@router.post("/api/devices/batch/ota/upgrade")
def api_batch_ota_upgrade(req: BatchOtaReq, request: Request, db: Session = Depends(get_db)):
    from backend.main import upgrade_ota_task
    _check_ota_batch_allowed(request, req.device_ids)
    existing_ids = [row.id for row in db.query(Device.id).filter(Device.id.in_(req.device_ids)).all()]
    executor = _get_shared_executor()
    results = list(executor.map(upgrade_ota_task, existing_ids))
    _audit("ota_upgrade", detail=f"count={len(existing_ids)} ips={[r.get('ip') for r in results]}")
    return {"results": results}
