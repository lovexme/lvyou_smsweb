"""Server-side notification dispatch.

Currently implements the WeChat Work (企业微信) ``/cgi-bin/message/send``
endpoint. The module is deliberately small: callers pass in the corp
credentials each call so credentials can live in the SQLite settings table.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import httpx

logger = logging.getLogger("notify")

WXWORK_BASE = "https://qyapi.weixin.qq.com/cgi-bin"

# In-process access_token cache: (corpid, secret) -> (token, expires_at)
_token_cache: dict[Tuple[str, str], Tuple[str, float]] = {}


@dataclass
class WxWorkConfig:
    corpid: str
    corpsecret: str
    agentid: str
    touser: str = "@all"


def _get_access_token(client: httpx.Client, cfg: WxWorkConfig) -> Optional[str]:
    key = (cfg.corpid, cfg.corpsecret)
    now = time.time()
    cached = _token_cache.get(key)
    if cached and cached[1] > now + 30:
        return cached[0]
    try:
        resp = client.get(
            f"{WXWORK_BASE}/gettoken",
            params={"corpid": cfg.corpid, "corpsecret": cfg.corpsecret},
            timeout=10.0,
        )
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("WxWork gettoken failed: %s", exc)
        return None
    if int(body.get("errcode", -1)) != 0:
        logger.warning("WxWork gettoken errcode=%s msg=%s",
                       body.get("errcode"), body.get("errmsg"))
        return None
    tok = str(body.get("access_token") or "")
    expires_in = int(body.get("expires_in") or 7200)
    if not tok:
        return None
    _token_cache[key] = (tok, now + max(60, expires_in - 60))
    return tok


def send_wxwork_text(client: httpx.Client, cfg: WxWorkConfig,
                     content: str) -> Tuple[bool, str]:
    """Send a plain-text message via WeChat Work. Returns (ok, error)."""
    if not cfg.corpid or not cfg.corpsecret or not cfg.agentid:
        return False, "WxWork 未配置"
    token = _get_access_token(client, cfg)
    if not token:
        return False, "获取 access_token 失败"
    payload = {
        "touser": cfg.touser or "@all",
        "msgtype": "text",
        "agentid": int(cfg.agentid),
        "text": {"content": content[:1900]},
        "safe": 0,
    }
    try:
        resp = client.post(
            f"{WXWORK_BASE}/message/send",
            params={"access_token": token},
            json=payload,
            timeout=10.0,
        )
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        return False, f"网络异常: {exc}"
    if int(body.get("errcode", -1)) != 0:
        return False, f"errcode={body.get('errcode')} msg={body.get('errmsg')}"
    return True, ""
