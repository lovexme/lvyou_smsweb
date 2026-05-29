"""Authentication routes.

Extracted from backend/main.py.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.config import (
    AUTH_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    TOKEN_TTL_SECONDS,
    UIPASS,
    UIUSER,
)
from backend.db import (
    delete_token as _delete_token,
    get_token_record as _get_token_record,
    issue_token as _issue_token,
    nowts,
)

logger = logging.getLogger("board-manager")

router = APIRouter()

# Injected from main.py to avoid circular imports
_client_ip = None
_login_limiter_ip = None
_login_limiter_user = None
_check_login_credentials = None
_extract_request_token = None
_set_auth_cookies = None
_clear_auth_cookies = None
_audit = None
_bm_login_failure = None
_csrf_for_token = None
_SessionLocal = None


def inject(
    *,
    client_ip=None,
    login_limiter_ip=None,
    login_limiter_user=None,
    check_login_credentials=None,
    extract_request_token=None,
    set_auth_cookies=None,
    clear_auth_cookies=None,
    audit=None,
    bm_login_failure=None,
    csrf_for_token=None,
    session_local=None,
):
    global _client_ip, _login_limiter_ip, _login_limiter_user
    global _check_login_credentials, _extract_request_token
    global _set_auth_cookies, _clear_auth_cookies, _audit
    global _bm_login_failure, _csrf_for_token, _SessionLocal
    if client_ip: _client_ip = client_ip
    if login_limiter_ip: _login_limiter_ip = login_limiter_ip
    if login_limiter_user: _login_limiter_user = login_limiter_user
    if check_login_credentials: _check_login_credentials = check_login_credentials
    if extract_request_token: _extract_request_token = extract_request_token
    if set_auth_cookies: _set_auth_cookies = set_auth_cookies
    if clear_auth_cookies: _clear_auth_cookies = clear_auth_cookies
    if audit: _audit = audit
    if bm_login_failure: _bm_login_failure = bm_login_failure
    if csrf_for_token: _csrf_for_token = csrf_for_token
    if session_local: _SessionLocal = session_local


class LoginReq:
    """Minimal login request model (avoids importing pydantic here)."""
    pass


@router.post("/api/login")
async def api_login(request: Request):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    client_ip = _client_ip(request)
    user_key = username.lower() if username else ""

    if not _login_limiter_ip.check_only(client_ip):
        _audit("login_blocked", user=username or "-", ip=client_ip, result="ratelimit_ip")
        _bm_login_failure("rate_limited_ip")
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")
    if user_key and not _login_limiter_user.check_only(user_key):
        _audit("login_blocked", user=username, ip=client_ip, result="ratelimit_user")
        _bm_login_failure("rate_limited_user")
        raise HTTPException(status_code=429, detail="该账号登录尝试过于频繁，请稍后再试")
    if not _check_login_credentials(username, password):
        _login_limiter_ip.record(client_ip)
        if user_key:
            _login_limiter_user.record(user_key)
        _audit("login_fail", user=username or "-", ip=client_ip, result="bad_credentials")
        _bm_login_failure("bad_credentials")
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if user_key:
        _login_limiter_user.reset(user_key)
    token = _issue_token(username)
    _audit("login", user=username, ip=client_ip, result="ok")

    response = JSONResponse(content={
        "ok": True, "token": token, "username": username, "expiresIn": TOKEN_TTL_SECONDS,
    })
    _set_auth_cookies(response, token)
    return response


@router.get("/api/me")
def api_me(request: Request):
    token, _ = _extract_request_token(request)
    record = _get_token_record(token) if token else None
    if not record:
        raise HTTPException(status_code=401, detail="未登录")
    return {
        "ok": True,
        "username": record.get("username", ""),
        "expiresIn": max(0, int(record.get("exp", 0)) - nowts()),
    }


@router.post("/api/logout")
def api_logout(request: Request):
    token, _ = _extract_request_token(request)
    if token:
        _delete_token(token)
        _audit("logout", ip=_client_ip(request), result="ok")
    response = JSONResponse(content={"ok": True})
    _clear_auth_cookies(response)
    return response


@router.get("/api/health")
def health():
    db_status = "ok"
    db_error: Optional[str] = None
    try:
        with _SessionLocal() as session:
            from sqlalchemy import text
            session.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = "error"
        db_error = type(exc).__name__
    payload: Dict[str, Any] = {
        "status": "ok" if db_status == "ok" else "degraded",
        "db": db_status,
        "version": "5.1",
        "message": "Board LAN Hub API is running",
    }
    if db_error:
        payload["db_error"] = db_error
    return JSONResponse(
        payload,
        status_code=200 if db_status == "ok" else 503,
    )
