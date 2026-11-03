"""Runtime settings shared by every code path that talks to a board.

The board admin credential used to be the compile-time ``DEFAULTPASS``. That
made onboarding fail in two ways: a board whose password had been rotated was
detected by the scanner but could never be queried afterwards, and a fresh
install had no way to enter the real password short of rebuilding the image.

The credential now lives in the ``settings`` table. Reads are cached per
process for a few seconds because the v4 and v6 uvicorn workers each keep their
own cache; the short TTL bounds how long the other worker can serve a stale
value after a save.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional, Tuple

from backend.config import DEFAULTPASS, DEFAULTUSER
from backend.db import SessionLocal, Setting

KEY_USER = "device_user"
KEY_PASS = "device_pass"

_CACHE_TTL_SECONDS = 5.0
_lock = threading.Lock()
_cache: Optional[Tuple[float, str, str]] = None


def _load(db) -> Tuple[str, str, bool]:
    rows = {
        row.key: row.value
        for row in db.query(Setting).filter(Setting.key.in_((KEY_USER, KEY_PASS))).all()
    }
    raw_user = rows.get(KEY_USER)
    raw_pass = rows.get(KEY_PASS)
    env_user = (os.environ.get("BMDEVUSER", "") or "").strip()
    env_pass = os.environ.get("BMDEVPASS", "") or ""
    user = raw_user if raw_user else (env_user or DEFAULTUSER)
    # An empty stored password means "not configured yet": take the value the
    # container was started with, then the documented factory default, so a
    # fresh install still probes boards. Deployments set BMDEVPASS in .env.
    password = raw_pass if raw_pass else (env_pass or DEFAULTPASS)
    configured = bool(raw_pass or env_pass)
    return user, password, configured


def get_device_credentials() -> Tuple[str, str]:
    """Return (user, password) used when a device row has no own credential."""
    global _cache
    now = time.monotonic()
    with _lock:
        if _cache and now - _cache[0] < _CACHE_TTL_SECONDS:
            return _cache[1], _cache[2]
    db = SessionLocal()
    try:
        user, password, _ = _load(db)
    finally:
        db.close()
    with _lock:
        _cache = (now, user, password)
    return user, password


def device_credentials_configured() -> bool:
    db = SessionLocal()
    try:
        _, _, configured = _load(db)
    finally:
        db.close()
    return configured


def set_device_credentials(user: str, password: str) -> None:
    """Persist the credential and drop the cache so the next call re-reads it."""
    global _cache
    user = (user or "").strip() or DEFAULTUSER
    db = SessionLocal()
    try:
        for key, value in ((KEY_USER, user), (KEY_PASS, password or "")):
            row = db.query(Setting).filter(Setting.key == key).first()
            if row is None:
                db.add(Setting(key=key, value=value))
            else:
                row.value = value
        db.commit()
    finally:
        db.close()
    with _lock:
        _cache = (time.monotonic(), user, password or DEFAULTPASS)


def device_credential(device) -> Tuple[str, str]:
    """Per-device credential with the stored default as fallback."""
    user, password = get_device_credentials()
    return ((device.user or user).strip(), (device.passwd or password).strip())
