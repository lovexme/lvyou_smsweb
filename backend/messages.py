"""SMS / call history: polling, webhook ingestion and notifications.

The boards keep a local ring buffer of SMS and call records and can also push
new events to a URL. lvyou-panel relied on both; this module brings the same
two paths to the new panel so history keeps working across a cutover:

* ``poll_all`` walks every board with ``querysms`` / ``querytel`` (catch-up).
* :func:`ingest_push` parses the board's webhook payload (real-time).

Both write through the same dedup key, so a message that arrives by push and
is also seen by the next poll is stored once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import httpx
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.db import CallRecord, Device, SessionLocal, Setting, SmsJob, SmsRecord
from backend.device_client import ensure_device_token
from backend.http_client import get_sync_client
from backend.settings_store import device_credential

logger = logging.getLogger("board-manager")

CST = timezone(timedelta(hours=8))

KEY_NOTIFY_WEBHOOK = "dingtalk_webhook"
KEY_NOTIFY_SECRET = "dingtalk_secret"
KEY_WEBHOOK_TOKEN = "webhook_token"
KEY_BARK_URL = "bark_url"

POLL_INTERVAL_SECONDS = 60
SMS_FETCH_LIMIT = 50


def _now_str() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def _ts_to_str(ts: int) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=CST).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return _now_str()


def _dedup(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row is None or row.value in (None, ""):
        return default
    return str(row.value)


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    db.commit()


# ── Board communication (legacy /ctrl API) ───────────────────────────────────

def device_call(
    ip: str,
    token: str,
    cmd: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    query = {"token": token, "cmd": cmd}
    if params:
        query.update({k: str(v) for k, v in params.items()})
    try:
        resp = get_sync_client().get(f"http://{ip}/ctrl", params=query, timeout=timeout)
        return resp.json()
    except Exception as exc:
        return {"code": -1, "note": str(exc)}


def device_label(device: Device) -> str:
    return (device.alias or "").strip() or (device.devId or "").strip() or device.ip


# ── Storage ──────────────────────────────────────────────────────────────────

def store_sms(
    db: Session,
    *,
    device: Optional[Device],
    dev_id: str,
    sim_slot: str,
    phone: str,
    content: str,
    direction: str,
    sms_ts: int,
    raw: Any,
) -> Tuple[bool, int]:
    """Insert one SMS; returns (inserted, record_id)."""
    dedup = _dedup(dev_id or (device and device.devId) or "-", sms_ts, sim_slot, direction, phone, content)
    stmt = (
        sqlite_insert(SmsRecord)
        .values(
            device_id=device.id if device else None,
            dev_id=dev_id or (device.devId if device else ""),
            sim_slot=sim_slot,
            phone=phone,
            content=content,
            direction=direction,
            raw_json=json.dumps(raw, ensure_ascii=False) if raw is not None else "",
            sms_ts=int(sms_ts or 0),
            sms_time=_ts_to_str(sms_ts) if sms_ts else _now_str(),
            created=_now_str(),
            dedup=dedup,
        )
        .on_conflict_do_nothing(index_elements=["dedup"])
    )
    result = db.execute(stmt)
    db.commit()
    if result.rowcount:
        row = db.query(SmsRecord).filter(SmsRecord.dedup == dedup).first()
        return True, (row.id if row else 0)
    return False, 0


def store_call(
    db: Session,
    *,
    device: Optional[Device],
    dev_id: str,
    sim_slot: str,
    phone: str,
    direction: str,
    action: str,
    duration: int,
    start_ts: int,
    raw: Any,
    update_existing: bool = False,
) -> Tuple[bool, int]:
    """Insert a call record. Webhook call-state updates set ``update_existing``."""
    ident = dev_id or (device.devId if device else "") or "-"
    dedup = _dedup(ident, "call", start_ts)
    if update_existing:
        row = db.query(CallRecord).filter(CallRecord.dedup == dedup).first()
        if row is not None:
            row.action = action or row.action
            row.duration = duration or row.duration
            row.raw_json = json.dumps(raw, ensure_ascii=False) if raw is not None else row.raw_json
            db.commit()
            return False, row.id
    stmt = (
        sqlite_insert(CallRecord)
        .values(
            device_id=device.id if device else None,
            dev_id=ident,
            sim_slot=sim_slot,
            phone=phone,
            direction=direction,
            action=action,
            duration=max(0, int(duration or 0)),
            raw_json=json.dumps(raw, ensure_ascii=False) if raw is not None else "",
            call_time=_ts_to_str(start_ts) if start_ts else _now_str(),
            start_ts=int(start_ts or 0),
            created=_now_str(),
            dedup=dedup,
        )
        .on_conflict_do_nothing(index_elements=["dedup"])
    )
    result = db.execute(stmt)
    db.commit()
    if result.rowcount:
        row = db.query(CallRecord).filter(CallRecord.dedup == dedup).first()
        return True, (row.id if row else 0)
    return False, 0


# ── Polling ──────────────────────────────────────────────────────────────────

def poll_device(db: Session, device: Device) -> Dict[str, int]:
    added_sms = 0
    added_calls = 0
    try:
        token = ensure_device_token(db, device)
    except Exception as exc:
        logger.debug("poll %s: no token (%s)", device.ip, exc)
        return {"sms": 0, "calls": 0}

    for slot in ("1", "2"):
        res = device_call(device.ip, token, "querysms", {"p1": "0", "p2": str(SMS_FETCH_LIMIT), "p4": slot})
        if res.get("code") != 0:
            continue
        results = res.get("results")
        if not isinstance(results, list):
            continue
        for msg in results:
            ts = int(msg.get("smsTs") or 0)
            if not ts:
                continue
            direction = "sent" if str(msg.get("dir")) == "1" else "received"
            inserted, _ = store_sms(
                db,
                device=device,
                dev_id=device.devId or "",
                sim_slot=f"sim{slot}",
                phone=str(msg.get("phNum") or ""),
                content=str(msg.get("smsBd") or ""),
                direction=direction,
                sms_ts=ts,
                raw=msg,
            )
            if inserted:
                added_sms += 1
                notify_sms(device_label(device), f"sim{slot}", str(msg.get("phNum") or ""),
                           str(msg.get("smsBd") or ""), direction, db=db)

    res = device_call(device.ip, token, "querytel", {"p1": "1", "p2": "50"}, timeout=12)
    if res.get("code") == 0 and isinstance(res.get("results"), list):
        for rec in res["results"]:
            start = int(rec.get("telStartTs") or 0)
            if not start:
                continue
            end = int(rec.get("telEndTs") or 0)
            answered = str(rec.get("conn")) == "1"
            direction = "outgoing" if str(rec.get("dir")) == "1" else "incoming"
            if answered:
                action = "answered"
            else:
                action = "missed" if direction == "incoming" else "no_answer"
            duration = max(0, end - start) if answered and end > start else 0
            inserted, _ = store_call(
                db,
                device=device,
                dev_id=device.devId or "",
                sim_slot=f"sim{rec.get('slot', 1)}",
                phone=str(rec.get("phNum") or ""),
                direction=direction,
                action=action,
                duration=duration,
                start_ts=start,
                raw=rec,
            )
            if inserted:
                added_calls += 1
    return {"sms": added_sms, "calls": added_calls}


def poll_all() -> Dict[str, Any]:
    db = SessionLocal()
    totals = {"devices": 0, "sms": 0, "calls": 0}
    try:
        devices = db.query(Device).all()
        for device in devices:
            totals["devices"] += 1
            try:
                got = poll_device(db, device)
                totals["sms"] += got["sms"]
                totals["calls"] += got["calls"]
            except Exception:
                db.rollback()
                logger.debug("poll device %s failed", device.ip, exc_info=True)
    finally:
        db.close()
    return totals


# ── Webhook ingestion ────────────────────────────────────────────────────────

CALL_EVENT_TYPES = {601, 602, 603, 620, 621, 622, 623}


def _find_device(db: Session, dev_id: str, source_ip: str = "") -> Optional[Device]:
    device = None
    if dev_id:
        device = db.query(Device).filter(Device.devId == dev_id).first()
    if device is None and source_ip:
        device = db.query(Device).filter(Device.ip == source_ip).first()
    return device


def ingest_push(db: Session, data: Dict[str, Any], source_ip: str = "") -> Dict[str, Any]:
    try:
        msg_type = int(data.get("type", 0))
    except (TypeError, ValueError):
        return {"status": "bad_request"}
    dev_id = str(data.get("devId") or "")
    device = _find_device(db, dev_id, source_ip)
    label = device_label(device) if device else (dev_id or source_ip or "unknown")

    if msg_type in (501, 502):
        sms_ts = int(data.get("smsTs") or 0)
        if sms_ts > 10_000_000_000:  # board sometimes sends milliseconds
            sms_ts //= 1000
        if not sms_ts:
            sms_ts = int(data.get("devSmsTs") or data.get("msgTs") or 0)
        direction = "received" if msg_type == 501 else "sent"
        slot = f"sim{data.get('slot', 1)}"
        phone = str(data.get("phNum") or "")
        content = str(data.get("smsBd") or "")
        inserted, rec_id = store_sms(
            db, device=device, dev_id=dev_id, sim_slot=slot, phone=phone,
            content=content, direction=direction, sms_ts=sms_ts, raw=data,
        )
        if inserted:
            notify_sms(label, slot, phone, content, direction,
                       msg_type=msg_type, db=db)
        if msg_type == 502:
            _update_sms_job(
                db, data,
                status="sent",
                delivery_id=_delivery_id(data.get("smsSendMr")),
                note="已提交运营商",
            )
        return {"status": "ok", "stored": inserted, "id": rec_id, "type": msg_type}

    if msg_type == 402 and str(data.get("subType")) == "16":
        code = int(data.get("code") or 0)
        if code == 0:
            _update_sms_job(
                db, data,
                status="sent",
                delivery_id=str(data.get("val") or ""),
                note="板子已发出",
            )
        else:
            reason = str(data.get("val") or data.get("note") or "发送失败")
            reason_cn = {
                "timeout": "运营商发送超时",
                "cancel": "发送被取消",
                "unknow": "发送结果未知",
            }.get(reason, reason)
            job = _update_sms_job(db, data, status="failed", note=reason_cn)
            if job is not None:
                send_bark(
                    "绿邮 · 短信发送失败",
                    f"{label} {job.slot}\n{job.phone}\n{reason_cn}",
                    db=db,
                )
                send_dingtalk(
                    f"⚠️ {label} 短信发送失败\n号码：{job.phone}\n原因：{reason_cn}",
                    db=db,
                )
        return {"status": "ok", "type": 402, "subType": 16}

    if msg_type == 503:
        sms_st = int(data.get("smsSt") or 0)
        job = _update_sms_job(
            db, data,
            status="delivered" if sms_st == 0 else "failed",
            delivery_id=_delivery_id(data.get("smsSendMr")),
            note="运营商确认送达" if sms_st == 0 else f"送达失败（smsSt={sms_st}）",
        )
        if job is not None:
            text = ("运营商确认送达" if sms_st == 0 else f"送达失败（smsSt={sms_st}）")
            send_bark(f"绿邮 · 短信{text}", f"{label} {job.slot}\n{job.phone}", db=db)
        return {"status": "ok", "type": 503, "smsSt": sms_st}

    if msg_type in CALL_EVENT_TYPES:
        slot = f"sim{data.get('slot', 1)}"
        phone = str(data.get("phNum") or "")
        start = int(data.get("telStartTs") or 0) or int(data.get("msgTs") or 0)
        end = int(data.get("telEndTs") or 0)
        connected = int(data.get("telConnectedTs") or 0)
        direction = "incoming" if str(data.get("telDir", "")) == "1" else "outgoing"
        if msg_type >= 620:
            direction = "outgoing"
        answered = connected > 0
        if answered:
            action = "answered"
        elif msg_type == 603:
            action = "missed"
        else:
            action = "ringing" if msg_type in (601, 620) else (
                "missed" if direction == "incoming" else "no_answer"
            )
        duration = max(0, end - connected) if answered and end else 0
        # A missed-call / unanswered-outgoing push repeats until the call
        # record reaches its final state; only announce the transition.
        prev_action = ""
        if msg_type in (603, 623):
            ident = dev_id or (device.devId if device else "") or "-"
            row = db.query(CallRecord).filter(CallRecord.dedup == _dedup(ident, "call", start)).first()
            prev_action = row.action if row else ""

        inserted, rec_id = store_call(
            db, device=device, dev_id=dev_id, sim_slot=slot, phone=phone,
            direction=direction, action=action, duration=duration,
            start_ts=start, raw=data, update_existing=msg_type not in (601, 620),
        )
        if msg_type in (601, 620):
            _notify_call(
                label, slot, phone,
                title="来电" if msg_type == 601 else "拨出",
                detail="响铃中" if msg_type == 601 else "正在呼叫",
                db=db,
            )
        elif msg_type == 603 and action == "missed" and prev_action != "missed":
            _notify_call(label, slot, phone, title="未接来电", detail="没有接听", db=db)
        elif msg_type == 623 and action == "missed" and prev_action != "missed":
            _notify_call(label, slot, phone, title="去电未接通", detail="对方未接或占线", db=db)
        return {"status": "ok", "stored": inserted, "id": rec_id, "type": msg_type}

    return {"status": "ignored", "type": msg_type}


def _delivery_id(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    return str(value or "")


def _update_sms_job(
    db: Session,
    data: Dict[str, Any],
    *,
    status: str,
    delivery_id: str = "",
    note: str = "",
) -> Optional[SmsJob]:
    """Apply an asynchronous board report to the matching sendsms job.

    Returns the job when its status changed, so callers can push a
    notification exactly once even if the board retries the webhook.
    """
    tid = str(data.get("tid") or "").strip()
    if not tid:
        return None
    job = db.query(SmsJob).filter(SmsJob.tid == tid).first()
    if job is None:
        return None
    # A 402 success followed by a 502/503 must not be downgraded.
    rank = {"queued": 0, "sent": 1, "delivered": 2, "failed": 3}
    if rank.get(status, 0) < rank.get(job.status, 0) and status != "failed":
        return None
    previous = job.status
    job.status = status
    if delivery_id:
        job.delivery_id = delivery_id[:128]
    if note:
        job.note = note[:255]
    job.updated = int(time.time())
    db.commit()
    return job if previous != status else None


# ── Notifications (DingTalk + Bark) ─────────────────────────────────────

def _sign_dingtalk(webhook_url: str, secret: str) -> str:
    if not secret:
        return webhook_url
    timestamp = str(round(time.time() * 1000))
    sign_string = f"{timestamp}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha256).digest()
    sign = urllib.parse.quote(base64.b64encode(digest))
    sep = "&" if "?" in webhook_url else "?"
    return f"{webhook_url}{sep}timestamp={timestamp}&sign={sign}"


def send_dingtalk(text: str, db: Optional[Session] = None) -> bool:
    own = db is None
    db = db or SessionLocal()
    try:
        url = get_setting(db, KEY_NOTIFY_WEBHOOK, "")
        secret = get_setting(db, KEY_NOTIFY_SECRET, "")
    finally:
        if own:
            db.close()
    if not url:
        return False
    try:
        resp = get_sync_client().post(
            _sign_dingtalk(url, secret),
            json={"msgtype": "text", "text": {"content": text}},
            timeout=5,
        )
        return resp.status_code == 200
    except Exception:
        logger.debug("dingtalk notify failed", exc_info=True)
        return False


def send_bark(title: str, body: str, db: Optional[Session] = None) -> bool:
    """Post one notification to the configured Bark URL.

    Bark may answer HTTP 200 while still rejecting the request in its JSON
    body, so both layers have to agree before this counts as delivered to the
    push service.
    """
    own = db is None
    db = db or SessionLocal()
    try:
        url = (get_setting(db, KEY_BARK_URL, "") or "").strip()
    finally:
        if own:
            db.close()
    if not url:
        return False
    try:
        resp = get_sync_client().post(
            url,
            json={"title": title, "body": body, "group": "绿邮"},
            timeout=8,
        )
        if resp.status_code != 200:
            logger.warning("bark notify HTTP %s", resp.status_code)
            return False
        payload = resp.json()
        ok = isinstance(payload, dict) and int(payload.get("code") or 0) == 200
        if not ok:
            logger.warning("bark notify rejected: code=%s", payload.get("code") if isinstance(payload, dict) else "?")
        return ok
    except Exception:
        logger.warning("bark notify failed", exc_info=True)
        return False


def notify_sms(dev_name: str, slot: str, phone: str, content: str,
               direction: str, msg_type: int = 0,
               db: Optional[Session] = None) -> None:
    emoji = "📩" if direction == "received" else "📤"
    label = "收到" if direction == "received" else "发出"
    text = f"{emoji} {dev_name} {label}短信\n卡槽：{slot}\n号码：{phone}\n内容：{content}"
    send_dingtalk(text, db=db)
    title = "新短信" if direction == "received" else "短信已发出"
    subtitle = "已提交运营商" if msg_type == 502 else label
    send_bark(f"绿邮 · {title}", f"{dev_name} {slot}\n{subtitle}\n{phone}\n{content}", db=db)


def _notify_call(dev_name: str, slot: str, phone: str, *, title: str,
                 detail: str, db: Optional[Session] = None) -> None:
    """Ring/missed/unanswered alert on both push channels."""
    text = f"📞 {dev_name} {title}：{phone}"
    send_dingtalk(text, db=db)
    body = "\n".join(x for x in (f"{dev_name} {slot}", detail, phone) if x)
    send_bark(f"绿邮 · {title}", body, db=db)


# ── Background loop ──────────────────────────────────────────────────────────

async def messages_poll_loop() -> None:
    import asyncio

    await asyncio.sleep(20)
    while True:
        try:
            stats = await asyncio.to_thread(poll_all)
            if stats["sms"] or stats["calls"]:
                logger.info("message poll: +%s sms, +%s calls", stats["sms"], stats["calls"])
        except Exception:
            logger.debug("message poll loop error", exc_info=True)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
