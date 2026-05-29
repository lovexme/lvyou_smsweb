"""Device configuration batch operations.

Extracted from backend/main.py.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.config import CONFIG_MAX_CHARS, DEFAULTPASS, DEFAULTUSER, OTA_BATCH_MAX, TIMEOUT
from backend.db import Device, get_db

logger = logging.getLogger("board-manager")

router = APIRouter()

# Injected from main.py
_get_sync_client = None
_get_shared_executor = None
_ensure_device_ip_allowed_raise = None
_istargetdevice = None
_read_device_config = None
_write_device_config = None
_device_conn_info = None
_audit = None


def inject(
    *,
    get_sync_client=None,
    get_shared_executor=None,
    ensure_device_ip_allowed_raise=None,
    is_target_device=None,
    read_device_config_fn=None,
    write_device_config_fn=None,
    device_conn_info=None,
    audit=None,
):
    global _get_sync_client, _get_shared_executor, _ensure_device_ip_allowed_raise
    global _istargetdevice, _read_device_config, _write_device_config
    global _device_conn_info, _audit
    if get_sync_client: _get_sync_client = get_sync_client
    if get_shared_executor: _get_shared_executor = get_shared_executor
    if ensure_device_ip_allowed_raise: _ensure_device_ip_allowed_raise = ensure_device_ip_allowed_raise
    if is_target_device: _istargetdevice = is_target_device
    if read_device_config_fn: _read_device_config = read_device_config_fn
    if write_device_config_fn: _write_device_config = write_device_config_fn
    if device_conn_info: _device_conn_info = device_conn_info
    if audit: _audit = audit


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


# ── Regex / config helpers ───────────────────────────────────────────────────

import regex as _user_regex

REGEX_TIMEOUT = float(__import__("os").environ.get("BMUSERREGEXTIMEOUT", "1.0"))


def _apply_regex(config: str, pattern: str, replacement: str, flags_str: str) -> Optional[str]:
    try:
        flags = 0
        for f in flags_str.lower():
            if f == "i":
                flags |= _user_regex.IGNORECASE
            elif f == "m":
                flags |= _user_regex.MULTILINE
            elif f == "s":
                flags |= _user_regex.DOTALL
            elif f.strip():
                return None
        return _user_regex.sub(pattern, replacement, config, flags=flags, timeout=REGEX_TIMEOUT)
    except (_user_regex.error, TimeoutError):
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


# ── Task functions ───────────────────────────────────────────────────────────

def config_read_task_sync(device_info: Dict[str, Any]) -> Dict[str, Any]:
    ip, user, pw = device_info["ip"], device_info["user"], device_info["pw"]
    try:
        config = _read_device_config(ip, user, pw)
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
        "id": device_info["id"], "ip": device_info["ip"], "ok": True,
        "original": config, "replaced": replaced, "changed": config != replaced,
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
        "id": device_info["id"], "ip": device_info["ip"], "ok": True,
        "original": config, "replaced": replaced, "changed": config != replaced,
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
    if not _write_device_config(device_info["ip"], device_info["user"], device_info["pw"], replaced):
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": "写入配置失败"}
    saved = _read_device_config(device_info["ip"], device_info["user"], device_info["pw"])
    if saved is None:
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": "写入后读取校验失败"}
    saved_error = _validate_config_content(original, saved)
    if saved_error:
        _write_device_config(device_info["ip"], device_info["user"], device_info["pw"], original)
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
    if not _write_device_config(device_info["ip"], device_info["user"], device_info["pw"], replaced):
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": "写入配置失败"}
    saved = _read_device_config(device_info["ip"], device_info["user"], device_info["pw"])
    if saved is None:
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": "写入后读取校验失败"}
    saved_error = _validate_config_content(original, saved)
    if saved_error:
        _write_device_config(device_info["ip"], device_info["user"], device_info["pw"], original)
        return {"id": device_info["id"], "ip": device_info["ip"], "ok": False, "error": f"写入后校验失败，已尝试恢复原配置：{saved_error}"}
    _audit("config_preset_write", detail=f"device={device_info['id']} ip={device_info['ip']} preset={preset}")
    return {"id": device_info["id"], "ip": device_info["ip"], "ok": True, "changed": True}


# ── Validation helpers ───────────────────────────────────────────────────────

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


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("/api/devices/batch/config/read")
def api_batch_config_read(req: BatchConfigReadReq, db: Session = Depends(get_db)):
    _check_config_device_ids(req.device_ids)
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    executor = _get_shared_executor()
    configs = list(executor.map(config_read_task_sync, infos))
    return {"configs": configs}


@router.post("/api/devices/batch/config/preview")
def api_batch_config_preview(req: BatchConfigPreviewReq, db: Session = Depends(get_db)):
    _validate_config_regex(req.pattern, req.replacement)
    _check_config_device_ids(req.device_ids)
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    executor = _get_shared_executor()
    previews = list(executor.map(lambda info: config_preview_task_sync(info, req.pattern, req.replacement, req.flags), infos))
    return {"previews": previews}


@router.post("/api/devices/batch/config/preset/preview")
def api_batch_config_preset_preview(req: BatchConfigPresetReq, db: Session = Depends(get_db)):
    _check_config_device_ids(req.device_ids)
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    executor = _get_shared_executor()
    previews = list(executor.map(lambda info: config_preset_preview_task_sync(info, req.preset), infos))
    return {"previews": previews}


@router.post("/api/devices/batch/config/write")
def api_batch_config_write(req: BatchConfigWriteReq, db: Session = Depends(get_db)):
    _validate_config_regex(req.pattern, req.replacement)
    _check_config_device_ids(req.device_ids)
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    _audit("batch_config_write", detail=f"count={len(infos)} pattern_len={len(req.pattern)}")
    executor = _get_shared_executor()
    results = list(executor.map(lambda info: config_write_task_sync(info, req.pattern, req.replacement, req.flags), infos))
    return {"results": results}


@router.post("/api/devices/batch/config/preset/write")
def api_batch_config_preset_write(req: BatchConfigPresetReq, db: Session = Depends(get_db)):
    _check_config_device_ids(req.device_ids)
    devices = db.query(Device).filter(Device.id.in_(req.device_ids)).all()
    infos = [_device_conn_info(d) for d in devices]
    _audit("batch_config_preset_write", detail=f"count={len(infos)} preset={req.preset}")
    executor = _get_shared_executor()
    results = list(executor.map(lambda info: config_preset_write_task_sync(info, req.preset), infos))
    return {"results": results}
