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
import re
import threading
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

_bark_client: Optional[httpx.Client] = None


def _get_bark_client() -> httpx.Client:
    """IPv4-only client for Bark.

    The N1 has no working IPv6 route, and httpx otherwise spends the whole
    timeout on a failed IPv6 handshake before falling back. Pinning the local
    address to 0.0.0.0 takes the IPv4 path immediately.
    """
    global _bark_client
    if _bark_client is None:
        _bark_client = httpx.Client(
            transport=httpx.HTTPTransport(local_address="0.0.0.0"),
            timeout=10,
        )
    return _bark_client
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
                sim_number = _pick_sim_number(device, f"sim{slot}", msg.get("msIsdn"))
                _learn_sim_number(db, device, f"sim{slot}", sim_number)
                notify_sms(device_label(device), f"sim{slot}", str(msg.get("phNum") or ""),
                           str(msg.get("smsBd") or ""), direction, db=db,
                           sim_number=sim_number)

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

_LOCAL_PHONE_RE = re.compile(r"号码[：:]\s*([+0-9]+)")
_LOCAL_DEV_RE = re.compile(r"来自设备[：:]\s*([0-9A-Za-z_-]+)")
_LOCAL_SLOT_RE = re.compile(r"卡\s*(\d)")


def _find_device(db: Session, dev_id: str, source_ip: str = "") -> Optional[Device]:
    device = None
    if dev_id:
        device = db.query(Device).filter(Device.devId == dev_id).first()
    if device is None and source_ip:
        device = db.query(Device).filter(Device.ip == source_ip).first()
    return device


def _ingest_board_local_push(db: Session, data: Dict[str, Any], source_ip: str) -> Dict[str, Any]:
    """Handle the board's Bark-style missed-call template.

    Some firmware builds render 603 through the board's own "local push"
    template instead of the raw event JSON. The template has no type field,
    but it names the caller and the device, which is enough to close the
    ringing record that the matching 601 opened.
    """
    body = str(data.get("body") or "")
    if "未接来电" not in body:
        return {"status": "ignored", "reason": "local-push"}
    phone_match = _LOCAL_PHONE_RE.search(body)
    if not phone_match:
        return {"status": "ignored", "reason": "local-push-no-phone"}
    phone = phone_match.group(1)
    dev_match = _LOCAL_DEV_RE.search(body)
    dev_id = dev_match.group(1) if dev_match else ""
    slot_match = _LOCAL_SLOT_RE.search(body)
    slot = f"sim{slot_match.group(1)}" if slot_match else "sim1"
    device = _find_device(db, dev_id, source_ip)
    label = device_label(device) if device else (dev_id or source_ip or "unknown")

    query = db.query(CallRecord).filter(CallRecord.action == "ringing")
    if device is not None:
        query = query.filter(CallRecord.device_id == device.id)
    elif dev_id:
        query = query.filter(CallRecord.dev_id == dev_id)
    else:
        return {"status": "ignored", "reason": "local-push-no-device"}
    push_digits = re.sub(r"[^0-9]", "", phone)
    rec = None
    for candidate in query.order_by(CallRecord.start_ts.desc()).limit(20).all():
        rec_digits = re.sub(r"[^0-9]", "", candidate.phone or "")
        if not rec_digits or not push_digits or rec_digits == push_digits:
            rec = candidate
            break
    if rec is None:
        return {"status": "ignored", "reason": "local-push-no-ringing"}

    sim_number = ""
    try:
        original = json.loads(rec.raw_json or "{}")
    except Exception:
        original = {}
    sim_number = _pick_sim_number(device, slot, original.get("msIsdn"))

    rec.action = "missed"
    rec.duration = 0
    clean = {k: v for k, v in data.items() if k not in ("device_key", "device_keys")}
    rec.raw_json = json.dumps(clean, ensure_ascii=False)
    db.commit()
    # The board already tried to push this itself; forward it through the
    # panel only when the call just ended, so stale payloads do not buzz.
    if time.time() - int(rec.start_ts or 0) < 180:
        _notify_call(label, slot, phone, title="未接来电", detail="没有接听",
                     db=db, sim_number=sim_number)
    return {"status": "ok", "stored": False, "id": rec.id, "type": "local-missed"}


def ingest_push(db: Session, data: Dict[str, Any], source_ip: str = "") -> Dict[str, Any]:
    if not data.get("type") and (data.get("body") or data.get("subtitle")):
        return _ingest_board_local_push(db, data, source_ip)
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
            sim_number = _pick_sim_number(device, slot, data.get("msIsdn"))
            _learn_sim_number(db, device, slot, sim_number)
            notify_sms(label, slot, phone, content, direction,
                       msg_type=msg_type, db=db,
                       sim_number=sim_number)
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
                _run_async(send_bark, "绿邮 · 短信发送失败",
                           f"{label} {job.slot}\n{job.phone}\n{reason_cn}")
                _run_async(send_dingtalk,
                           f"⚠️ {label} 短信发送失败\n号码：{job.phone}\n原因：{reason_cn}")
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
            _run_async(send_bark, f"绿邮 · 短信{text}", f"{label} {job.slot}\n{job.phone}")
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
        sim_number = _pick_sim_number(device, slot, data.get("msIsdn"))
        _learn_sim_number(db, device, slot, sim_number)
        if msg_type in (601, 620):
            _notify_call(
                label, slot, phone,
                title="来电" if msg_type == 601 else "拨出",
                detail="响铃中" if msg_type == 601 else "正在呼叫",
                db=db, sim_number=sim_number,
            )
        elif msg_type == 603 and action == "missed" and prev_action != "missed":
            _notify_call(label, slot, phone, title="未接来电", detail="没有接听",
                         db=db, sim_number=sim_number)
        elif msg_type == 623 and action == "missed" and prev_action != "missed":
            _notify_call(label, slot, phone, title="去电未接通", detail="对方未接或占线",
                         db=db, sim_number=sim_number)
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


def send_bark(title: str, body: str, db: Optional[Session] = None,
              extra: Optional[dict] = None) -> bool:
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
    payload_data = {"title": title, "body": body, "group": "绿邮"}
    if extra:
        payload_data.update(extra)
    last_error = None
    for _attempt in range(2):
        try:
            resp = _get_bark_client().post(url, json=payload_data, timeout=10)
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                continue
            payload = resp.json()
            ok = isinstance(payload, dict) and int(payload.get("code") or 0) == 200
            if ok:
                return True
            last_error = f"code={payload.get('code') if isinstance(payload, dict) else '?'}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    logger.warning("bark notify failed after retry: %s", last_error)
    return False


def _run_async(fn, *args, **kwargs) -> None:
    """Fire a notification without holding the board's webhook request.

    The boards give a push only a few seconds before they retry it. DingTalk
    and Bark can each take several seconds, which used to make the board log
    HTTP -11 and re-send the same 601 while the call state moved on.
    """
    def run():
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.debug("async notify failed", exc_info=True)

    threading.Thread(target=run, daemon=True, name="notify").start()


def _slot_index(slot: str) -> str:
    tail = str(slot or "").strip()[-1:]
    return tail if tail in ("1", "2") else "1"


def _display_sim(slot: str, sim_number: str = "") -> str:
    label = f"卡{_slot_index(slot)}"
    digits = re.sub(r"[^0-9+]", "", sim_number or "")
    return f"{label} · 本机 {digits}" if len(digits) >= 5 else label


def _pick_sim_number(device: Optional[Device], slot: str, value: Any = "") -> str:
    number = str(value or "").strip()
    if number:
        return number
    if device is None:
        return ""
    attr = "sim1number" if _slot_index(slot) == "1" else "sim2number"
    return str(getattr(device, attr, "") or "")


def _learn_sim_number(db: Optional[Session], device: Optional[Device], slot: str,
                      number: str) -> None:
    """Remember a number the board revealed once, so later alerts can show it.

    The firmware never reports its own MSISDN through ``stat`` / ``readcard``;
    the only place it shows up is inside some SMS pushes. Persist it the first
    time we see one and keep the DB value (the user may have edited it).
    """
    digits = re.sub(r"[^0-9+]", "", number or "")
    if db is None or device is None or len(digits) < 5:
        return
    attr = "sim1number" if _slot_index(slot) == "1" else "sim2number"
    if str(getattr(device, attr, "") or "").strip():
        return
    try:
        setattr(device, attr, digits)
        db.commit()
    except Exception:
        db.rollback()
        logger.debug("learn sim number failed", exc_info=True)


def notify_sms(dev_name: str, slot: str, phone: str, content: str,
               direction: str, msg_type: int = 0,
               db: Optional[Session] = None, sim_number: str = "") -> None:
    emoji = "📩" if direction == "received" else "📤"
    label = "收到" if direction == "received" else "发出"
    text = f"{emoji} {dev_name} {label}短信\n卡槽：{_display_sim(slot, sim_number)}\n号码：{phone}\n内容：{content}"
    _run_async(send_dingtalk, text)
    title = "新短信" if direction == "received" else "短信已发出"
    subtitle = "已提交运营商" if msg_type == 502 else label
    _run_async(send_bark, f"绿邮 · {title}",
               f"{dev_name} · {_display_sim(slot, sim_number)}\n{subtitle}\n{phone}\n{content}")


def _notify_call(dev_name: str, slot: str, phone: str, *, title: str,
                 detail: str, db: Optional[Session] = None,
                 sim_number: str = "") -> None:
    """Ring/missed/unanswered alert on both push channels."""
    text = f"📞 {dev_name} {title}：{phone}（{_display_sim(slot, sim_number)}）"
    _run_async(send_dingtalk, text)
    # The number is the title; repeating it in the body doubles the alert on
    # the lock screen. Body carries only the source and the call state.
    body = "\n".join(x for x in (f"{dev_name} · {_display_sim(slot, sim_number)}", detail) if x)
    # Put the number in the title: that is the only line iOS shows when the
    # banner is collapsed, and it is what the user needs to decide to pick up.
    head = f"{title} {phone}".strip() if phone else f"绿邮 · {title}"
    extra = {"level": "timeSensitive"}
    if title == "来电":
        extra["call"] = "1"
    # Bark opens `url` when the alert is tapped. A tel: link turns the
    # notification into a one-tap callback from the user's own phone; the
    # board has no API to bridge live call audio elsewhere.
    dialable = re.sub(r"[^0-9+]", "", phone or "")
    if len(dialable) >= 5:
        extra["url"] = f"tel:{dialable}"
    _run_async(send_bark, head, body, None, extra)


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
