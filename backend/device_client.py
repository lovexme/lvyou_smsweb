"""Device HTTP communication primitives.

FIX(P2#4 refactor): extracted from ``backend/main.py``. Every outbound call to
a managed board lives here -- probing/auth (``istargetdevice``), reading state
(``getdevicedata`` / ``get_wifi_info`` / ``read_device_config``), writing config
(``write_device_config``), OTA (``ota_check`` + the per-device task helpers) and
token fetch. All of them route through :func:`ensure_device_ip_allowed` first so
the SSRF allow-list cannot be bypassed, and all reuse the shared connection-
pooled client from :mod:`backend.http_client`.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, Optional, Tuple

import httpx
from fastapi import HTTPException

from backend.config import (
    DEFAULTPASS,
    DEFAULTUSER,
    SCAN_RETRIES,
    SCAN_RETRY_SLEEP_MS,
    TIMEOUT,
)
from backend.db import Device, SessionLocal
from backend.http_client import get_sync_client
from backend.security import is_device_ip_allowed

logger = logging.getLogger("board-manager")


# The thin HTTP-aware wrapper that converts an allow-list failure into an
# HTTPException; the pure check lives in backend.security.
def ensure_device_ip_allowed(ip: str) -> None:
    if not is_device_ip_allowed(ip):
        logger.warning("blocked outbound device request to non-whitelisted ip: %s", ip)
        raise HTTPException(status_code=400, detail="设备 IP 不在允许的内网范围内")


# FIX(P1#9): reuse shared httpx.Client instead of creating one per call.
def istargetdevice(ip: str, user: str, pw: str) -> Tuple[bool, Optional[str]]:
    ensure_device_ip_allowed(ip)
    url = f"http://{ip}/mgr"
    last_realm: Optional[str] = None
    client = get_sync_client()
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
    ensure_device_ip_allowed(ip)
    keys_list = ["DEV_ID", "DEV_VER", "SIM1_PHNUM", "SIM2_PHNUM", "SIM1_OP", "SIM2_OP", "SIM1_STA", "SIM2_STA", "SIM1_SIGNAL", "SIM2_SIGNAL", "WIFI_NAME", "WIFI_DBM"]
    body = f"keys={json.dumps({'keys': keys_list}, ensure_ascii=False)}"
    try:
        resp = get_sync_client().post(
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
    ensure_device_ip_allowed(ip)
    keys_list = ["WIFI_NAME", "WIFI_DBM"]
    body = f"keys={json.dumps({'keys': keys_list}, ensure_ascii=False)}"
    try:
        resp = get_sync_client().post(
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
    ensure_device_ip_allowed(ip)
    body = f"keys={json.dumps({'keys': ['PROPF_1_1_1']}, ensure_ascii=False)}"
    try:
        resp = get_sync_client().post(
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
    ensure_device_ip_allowed(ip)
    try:
        resp = get_sync_client().post(
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


# ── OTA ──────────────────────────────────────────────────────────────────────
def ota_check(ip: str, user: str, pw: str) -> dict:
    ensure_device_ip_allowed(ip)
    resp = get_sync_client().get(
        f"http://{ip}/ota",
        params={"a": "chkNewVer"},
        auth=httpx.DigestAuth(user, pw),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    return data if isinstance(data, dict) else {}


def check_ota_task(device_id: int) -> dict:
    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            return {"id": device_id, "ok": False, "error": "设备不存在"}
        ip = device.ip
        user = (device.user or DEFAULTUSER).strip()
        pw = (device.passwd or DEFAULTPASS).strip()
        try:
            data = ota_check(ip, user, pw)
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
    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            return {"id": device_id, "ok": False, "error": "设备不存在"}
        ip = device.ip
        user = (device.user or DEFAULTUSER).strip()
        pw = (device.passwd or DEFAULTPASS).strip()
        try:
            data = ota_check(ip, user, pw)
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
            upgrade_resp = get_sync_client().get(
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


# ── Device token ───────────────────────────────────────────────────────────
def ensure_device_token(db, device) -> str:
    token = (getattr(device, "token", "") or "").strip()
    if token:
        return token
    user = (getattr(device, "user", "") or DEFAULTUSER).strip()
    pw = (getattr(device, "passwd", "") or DEFAULTPASS).strip()
    ensure_device_ip_allowed(device.ip)
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
    ensure_device_ip_allowed(ip)
    body = b"keys=%7B%22keys%22%3A%5B%22TOKEN%22%5D%7D"
    resp = get_sync_client().post(
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
