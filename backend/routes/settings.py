"""Settings that apply to every board rather than to one device row.

Right now that is the board admin credential. It is deliberately never
returned over the API: the UI only learns whether a password is configured, so
a logged-in browser session cannot read the boards' password back out of the
panel.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db import Device, SessionLocal
from backend.settings_store import (
    device_credentials_configured,
    get_device_credentials,
    set_device_credentials,
)

logger = logging.getLogger("board-manager")

router = APIRouter()


class DeviceCredentialReq(BaseModel):
    user: str = "admin"
    password: str = ""
    # Default true: this home setup runs five boards that share one admin
    # password, so a single save should make every board usable at once.
    applyToAll: bool = True


@router.get("/api/settings/device")
def get_device_setting():
    user, _ = get_device_credentials()
    return {"ok": True, "user": user, "passwordConfigured": device_credentials_configured()}


@router.post("/api/settings/device")
def save_device_setting(req: DeviceCredentialReq):
    password = req.password
    if not password:
        raise HTTPException(status_code=400, detail="设备管理员密码不能为空")
    if len(password) > 64:
        raise HTTPException(status_code=400, detail="设备管理员密码过长")

    set_device_credentials(req.user, password)

    cleared = 0
    if req.applyToAll:
        # Clearing the per-device copy makes each row inherit the shared
        # credential, so a later password change here applies everywhere
        # without touching 5 rows by hand.
        db = SessionLocal()
        try:
            rows = db.query(Device).all()
            for row in rows:
                if row.passwd or row.user:
                    row.passwd = ""
                    row.user = ""
                    cleared += 1
            db.commit()
        finally:
            db.close()

    return {"ok": True, "user": req.user.strip() or "admin", "passwordConfigured": True, "devicesReset": cleared}


class DeviceProbeReq(BaseModel):
    ip: str
    user: Optional[str] = None
    password: Optional[str] = None


@router.post("/api/settings/device/probe")
def probe_device(req: DeviceProbeReq):
    """Check one IP with the stored credential (or an explicitly supplied one).

    Used while adding a new board: it answers "password right / wrong" without
    adding the device, so a typo never ends up as an unusable row.
    """
    from backend.device_client import istargetdevice

    ip = (req.ip or "").strip()
    if not ip:
        raise HTTPException(status_code=400, detail="IP 不能为空")
    default_user, default_pass = get_device_credentials()
    user = (req.user or "").strip() or default_user
    password = req.password if req.password else default_pass
    try:
        ok, realm = istargetdevice(ip, user, password)
    except HTTPException:
        raise
    except Exception as exc:  # network error, timeout, ...
        logger.info("probe %s failed: %s", ip, exc)
        return {"ok": False, "reachable": False, "reason": str(exc)}
    return {"ok": bool(ok), "reachable": True, "realm": realm or ""}
