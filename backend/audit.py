"""Structured audit logging.

FIX(P2#4 refactor): extracted from ``backend/main.py``.

FIX(P2#3): file-backed structured audit log with size-based rotation.
Defaults colocate audit.log next to the SQLite DB so the systemd unit's
existing ReadWritePaths covers it without further changes; operators can
redirect via BMAUDITLOGFILE (e.g. to /var/log/board-manager/) or disable file
logging entirely with BMAUDITLOGDISABLE=1 (e.g. inside a container where stdout
shipping is preferred).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

from backend.config import DBPATH

logger = logging.getLogger("board-manager")

_audit_default_dir = os.path.dirname(DBPATH) or "/var/log/board-manager"
AUDIT_LOG_FILE         = os.environ.get("BMAUDITLOGFILE", os.path.join(_audit_default_dir, "audit.log"))
AUDIT_LOG_MAX_BYTES    = int(os.environ.get("BMAUDITLOGMAXBYTES",    str(10 * 1024 * 1024)))
AUDIT_LOG_BACKUP_COUNT = int(os.environ.get("BMAUDITLOGBACKUPCOUNT", "5"))
AUDIT_LOG_DISABLE      = os.environ.get("BMAUDITLOGDISABLE", "").strip().lower() in {"1", "true", "yes", "on"}


class _JsonAuditFormatter(logging.Formatter):
    """FIX(P2#3): emit one JSON object per line so log shippers (Loki,
    ELK, Datadog, journald JSON parser) can index fields without the
    fragile `key=value` regex parsing the legacy text format required."""

    def format(self, record):
        ts = datetime.utcfromtimestamp(record.created).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        payload = {
            "ts":     ts,
            "level":  record.levelname,
            "action": getattr(record, "audit_action", record.getMessage()),
            "user":   getattr(record, "audit_user", "-"),
            "detail": getattr(record, "audit_detail", ""),
        }
        ip = getattr(record, "audit_ip", "")
        if ip:
            payload["ip"] = ip
        result = getattr(record, "audit_result", "")
        if result:
            payload["result"] = result
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _setup_audit_logger() -> logging.Logger:
    """FIX(P2#3): configure the audit logger with two sinks: (a) a stream
    handler so journalctl still shows recent activity, and (b) a rotating
    file handler with the JSON formatter so the audit trail survives journal
    rotation and can be shipped externally. The function is idempotent so
    repeated imports (eg. pytest collection) don't pile up duplicate
    handlers."""

    aud = logging.getLogger("audit")
    aud.setLevel(logging.INFO)
    aud.propagate = False  # don't double-emit through the root logger
    if aud.handlers:
        return aud

    stream = logging.StreamHandler()
    # FIX(P2#3): include ip + result so the journal-readable form does
    # not drop the structured fields the JSON formatter persists.
    stream.setFormatter(logging.Formatter(
        "audit ts=%(asctime)s action=%(audit_action)s user=%(audit_user)s "
        "ip=%(audit_ip)s result=%(audit_result)s detail=%(audit_detail)s"
    ))
    aud.addHandler(stream)

    if AUDIT_LOG_DISABLE:
        return aud

    try:
        log_dir = os.path.dirname(AUDIT_LOG_FILE)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        fh = RotatingFileHandler(
            AUDIT_LOG_FILE,
            maxBytes=AUDIT_LOG_MAX_BYTES,
            backupCount=AUDIT_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setFormatter(_JsonAuditFormatter())
        aud.addHandler(fh)
    except OSError as exc:
        # Don't fail startup just because the audit dir isn't writable;
        # the stream handler keeps records visible and the warning lands
        # in the operator's main service log.
        logger.warning("audit file logger disabled (%s): %s", AUDIT_LOG_FILE, exc)
    return aud


_audit_logger = _setup_audit_logger()


def audit(action: str, user: str = "-", detail: str = "", ip: str = "", result: str = "") -> None:
    """FIX(P2#3): all audit call sites flow through structured fields
    (`extra=...`) instead of being baked into the message string."""
    _audit_logger.info(
        action,
        extra={
            "audit_action": action,
            "audit_user":   user,
            "audit_detail": detail,
            "audit_ip":     ip,
            "audit_result": result,
        },
    )
