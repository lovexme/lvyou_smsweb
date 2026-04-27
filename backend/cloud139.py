"""cloud.139.com (移动云手机) keep-alive helpers.

The H5 client at https://cloud.139.com/?channelSrc=02047 talks to
``https://cloud.139.com/ulhw/cloudphone/...`` with three layers of headers:

* ``sign``           = MD5(requestId + appId + token + secret).lower()
* ``x-kpcc-signature`` = base64(HMAC_SHA256(clientKey,
                                            f"{clientId}:{requestId}:{ts_ms}"))
* ``x-kpcc-timestamp`` / ``x-kpcc-requestid`` / ``x-kpcc-clientid`` … static
  per-session client identifiers.

All four constants below were extracted from the published JS bundle and
re-verified against four real captures in 2026-04. They are deliberately
hard-coded — the JS does not rotate them per session.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import random
import string
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("cloud139")

CLOUD139_BASE = "https://cloud.139.com/ulhw/cloudphone"

# Constants extracted from cloud.139.com JS bundle (channelSrc=02047 / cloudAppList).
APP_ID = "12345678"
SECRET = "e15a3bab3a70a4fe4ae17d4369f92a45"
CLIENT_ID = "X3qUAu6hA1yO6CegzcMSrQJknuZ7aEs1"
CLIENT_KEY = "AqXZ3iQtf713yN70ydXRZkCkxdeGo3jo"
CHANNEL_SRC = "02047"
PLATFORM = "h5"

# Mimics the official Web client's UA so the gateway accepts the request.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 10.0


def _request_id() -> str:
    """Replicate the JS ``h() + p(8)`` request-id format.

    h() returns ``YYYYMMDDhhmmss + epoch_ms`` rendered in CST (UTC+8),
    p(8) appends 8 alphanumeric characters.
    """
    cn_now = datetime.now(timezone(timedelta(hours=8)))
    rand = "".join(random.choices(string.ascii_letters + string.digits, k=8))
    return cn_now.strftime("%Y%m%d%H%M%S") + str(int(time.time() * 1000)) + rand


def build_headers(token: str = "") -> Dict[str, str]:
    """Produce the full kpcc/sign header set for a single request."""
    ts_ms = int(time.time() * 1000)
    request_id = _request_id()
    sign = hashlib.md5(
        (request_id + APP_ID + token + SECRET).encode()
    ).hexdigest().lower()
    x_req = uuid.uuid4().hex[:20]
    msg = f"{CLIENT_ID}:{x_req}:{ts_ms}"
    x_sig = base64.b64encode(
        hmac.new(CLIENT_KEY.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json; charset=utf-8",
        "appId": APP_ID,
        "requestId": request_id,
        "platform": PLATFORM,
        "token": token,
        "sign": sign,
        "x-kpcc-clientid": CLIENT_ID,
        "x-kpcc-requestid": x_req,
        "x-kpcc-timestamp": str(ts_ms),
        "x-kpcc-signature": x_sig,
        "x-channelSrc": CHANNEL_SRC,
        "Origin": "https://cloud.139.com",
        "Referer": "https://cloud.139.com/",
        "User-Agent": USER_AGENT,
    }


# ---------------------------------------------------------------------------
# JWT decoding (signature is opaque to us — we only need the payload claims)
# ---------------------------------------------------------------------------
def _b64url_decode(seg: str) -> bytes:
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg + pad)


def decode_jwt_payload(token: str) -> Dict[str, Any]:
    """Best-effort JWT payload decoder. Returns ``{}`` when the token does not
    parse — never raises — so that callers can still store an opaque token.
    """
    parts = (token or "").split(".")
    if len(parts) < 2:
        return {}
    try:
        return json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def jwt_account_summary(token: str) -> Dict[str, Any]:
    """Return a small dict describing a JWT for UI display.

    Fields: ``account_hash``, ``sub_id``, ``exp``, ``login_method``,
    ``client_type``. All values are strings or numbers; missing fields default
    to empty strings / 0.
    """
    p = decode_jwt_payload(token)
    return {
        "account_hash": str(p.get("account") or "")[:16],
        "sub_id": str(p.get("subId") or p.get("sub") or ""),
        "exp": int(p.get("exp") or 0),
        "login_method": str(p.get("loginMethod") or ""),
        "client_type": str(p.get("clientType") or ""),
    }


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    ok: bool
    status: int
    err_code: str
    err_msg: str
    raw: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "http_status": self.status,
            "code": self.err_code,
            "message": self.err_msg,
        }


def _parse_response(resp_text: str) -> Dict[str, Any]:
    try:
        return json.loads(resp_text)
    except Exception:  # noqa: BLE001
        return {}


def check_token(client: httpx.Client, token: str) -> CheckResult:
    """Call ``POST /user/checkToken`` with the given JWT.

    Verified working with a real session JWT in 2026-04-27. Empty body is
    intentional — that matches what the H5 page sends.
    """
    if not token:
        return CheckResult(False, 0, "EMPTY", "token 为空", "")
    headers = build_headers(token)
    try:
        resp = client.post(
            f"{CLOUD139_BASE}/user/checkToken",
            headers=headers,
            content="",
            timeout=REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        return CheckResult(False, 0, "NETWORK", str(exc)[:200], "")

    body = _parse_response(resp.text)
    header = body.get("header") or {}
    err_code = str(header.get("status") or "")
    err_msg = str(header.get("errMsg") or "")
    data = body.get("data") or {}
    success = bool(isinstance(data, dict) and data.get("success"))
    ok = resp.status_code == 200 and err_code == "200" and success
    return CheckResult(ok, resp.status_code, err_code, err_msg, resp.text[:600])
