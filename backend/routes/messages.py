"""SMS / call history API and the board webhook endpoint."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.db import CallRecord, Device, SessionLocal, SmsRecord, get_db
from backend.messages import (
    KEY_NOTIFY_SECRET,
    KEY_NOTIFY_WEBHOOK,
    KEY_WEBHOOK_TOKEN,
    get_setting,
    ingest_push,
    notify_sms,
    poll_device,
    send_dingtalk,
    set_setting,
)
from backend.security import client_ip_from_request, is_device_ip_allowed
from backend.settings_store import device_credential

logger = logging.getLogger("board-manager")

router = APIRouter()


def _device_label(db: Session, device_id: Optional[int], dev_id: str) -> str:
    device = None
    if device_id:
        device = db.query(Device).filter(Device.id == device_id).first()
    if device is None and dev_id:
        device = db.query(Device).filter(Device.devId == dev_id).first()
    if device is None:
        return dev_id or "-"
    return device.alias or device.devId or device.ip


@router.get("/api/messages/sms")
def api_sms_history(
    device_id: int = Query(0, ge=0),
    q: str = Query("", max_length=128),
    direction: str = Query("", max_length=16),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(SmsRecord)
    if device_id:
        query = query.filter(SmsRecord.device_id == device_id)
    if direction in ("sent", "received"):
        query = query.filter(SmsRecord.direction == direction)
    if q:
        like = f"%{q}%"
        query = query.filter(SmsRecord.phone.like(like) | SmsRecord.content.like(like))
    total = query.count()
    rows = (
        query.order_by(SmsRecord.sms_ts.desc(), SmsRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
        "items": [
            {
                "id": r.id,
                "device_id": r.device_id,
                "dev_id": r.dev_id,
                "device_name": _device_label(db, r.device_id, r.dev_id),
                "sim_slot": r.sim_slot,
                "phone": r.phone,
                "content": r.content,
                "direction": r.direction,
                "sms_ts": r.sms_ts,
                "sms_time": r.sms_time,
            }
            for r in rows
        ],
    }


@router.get("/api/messages/calls")
def api_call_history(
    device_id: int = Query(0, ge=0),
    q: str = Query("", max_length=128),
    direction: str = Query("", max_length=16),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(CallRecord)
    if device_id:
        query = query.filter(CallRecord.device_id == device_id)
    if direction in ("incoming", "outgoing"):
        query = query.filter(CallRecord.direction == direction)
    if q:
        query = query.filter(CallRecord.phone.like(f"%{q}%"))
    total = query.count()
    rows = (
        query.order_by(CallRecord.start_ts.desc(), CallRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
        "items": [
            {
                "id": r.id,
                "device_id": r.device_id,
                "dev_id": r.dev_id,
                "device_name": _device_label(db, r.device_id, r.dev_id),
                "sim_slot": r.sim_slot,
                "phone": r.phone,
                "direction": r.direction,
                "action": r.action,
                "duration": r.duration,
                "start_ts": r.start_ts,
                "call_time": r.call_time,
            }
            for r in rows
        ],
    }


@router.post("/api/messages/refresh/{device_id}")
def api_messages_refresh(device_id: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if device is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    stats = poll_device(db, device)
    return {"ok": True, **stats}


@router.get("/api/messages/notify")
def api_notify_get(db: Session = Depends(get_db)):
    return {
        "notify_configured": bool(get_setting(db, KEY_NOTIFY_WEBHOOK, "")),
        "notify_secret_configured": bool(get_setting(db, KEY_NOTIFY_SECRET, "")),
        "webhook_token_configured": bool(get_setting(db, KEY_WEBHOOK_TOKEN, "")),
    }


@router.post("/api/messages/notify")
async def api_notify_set(payload: Dict[str, Any], db: Session = Depends(get_db)):
    """Save DingTalk webhook / secret and the board push token.

    Empty string clears a value; absent keys are left untouched.
    ``rotate_webhook_token`` generates a fresh push token and returns it once.
    """
    if "dingtalk_webhook" in payload:
        set_setting(db, KEY_NOTIFY_WEBHOOK, str(payload.get("dingtalk_webhook") or "").strip())
    if "dingtalk_secret" in payload:
        set_setting(db, KEY_NOTIFY_SECRET, str(payload.get("dingtalk_secret") or "").strip())
    if "webhook_token" in payload:
        set_setting(db, KEY_WEBHOOK_TOKEN, str(payload.get("webhook_token") or "").strip())
    response: Dict[str, Any] = {"ok": True}
    if payload.get("rotate_webhook_token"):
        token = secrets.token_urlsafe(24)
        set_setting(db, KEY_WEBHOOK_TOKEN, token)
        response["webhook_token"] = token
    if payload.get("test"):
        response["test_ok"] = await asyncio.to_thread(send_dingtalk, "绿邮面板通知测试 ✅", db)
    return response


def _webhook_authorized(request: Request, db: Session, payload: Dict[str, Any]) -> bool:
    expected = (get_setting(db, KEY_WEBHOOK_TOKEN, "") or "").strip()
    if expected:
        provided = str(
            request.query_params.get("k")
            or request.headers.get("x-webhook-token", "")
            or payload.get("k")
            or ""
        ).strip()
        # Once a token is configured it is the only accepted credential: a
        # request from the LAN with a wrong or missing token must not be
        # ingested, otherwise any device on the network can forge history.
        return bool(provided) and secrets.compare_digest(provided, expected)
    ip = client_ip_from_request(request)
    if not is_device_ip_allowed(ip):
        return False
    # No token configured yet: only LAN sources may push (onboarding default).
    return True


@router.post("/webhook")
async def api_webhook(request: Request):
    """Receive pushes from the boards (SMS, call state, sent-SMS result)."""
    ct = request.headers.get("content-type", "")
    try:
        if "application/json" in ct:
            data = await request.json()
        else:
            body = (await request.body()).decode("utf-8", "replace")
            from urllib.parse import unquote
            data = dict(parse_qsl(unquote(body)))
    except Exception:
        return {"status": "bad_request"}
    if not isinstance(data, dict):
        return {"status": "bad_request"}

    db = SessionLocal()
    try:
        if not _webhook_authorized(request, db, data):
            raise HTTPException(status_code=403, detail="webhook source not allowed")
        return await asyncio.to_thread(ingest_push, db, data, client_ip_from_request(request))
    finally:
        db.close()
