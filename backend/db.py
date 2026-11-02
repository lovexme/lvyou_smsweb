"""SQLite-backed persistence layer.

FIX(P2#4): extracted from ``backend/main.py`` so the engine, models and
token CRUD live in their own module. Keeping this layer free of FastAPI
imports lets unit tests construct a session directly without spinning
up the full app.

The module is imported eagerly by ``backend/main.py`` *and* the audit
log defaults; nothing in here pulls anything back from main, so there
is no risk of the circular-import / dead-code regression that the
abandoned ``backend/app/`` attempt suffered from.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any, Dict, Optional

from sqlalchemy import (
    BigInteger,
    Column,
    Integer,
    String,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from backend.config import DBPATH, TOKEN_TTL_SECONDS

logger = logging.getLogger("board-manager")

Base = declarative_base()
engine = create_engine(
    f"sqlite:///{DBPATH}",
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"check_same_thread": False},
)


# FIX(H1): SQLite ships with rollback-journal mode and busy_timeout=0, so any
# concurrent writer (concurrent scans, batch SIM write-back, the v4/v6 process
# pair, /metrics count queries) immediately raised "database is locked" and the
# upsert path swallowed it -> scanned devices silently failed to save. Enable
# WAL (readers don't block the writer) and a 5s busy_timeout (writers wait for
# the lock instead of erroring) on every pooled connection.
@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Device(Base):
    __tablename__ = "devices"
    id           = Column(Integer, primary_key=True, index=True)
    devId        = Column(String(128), unique=True, nullable=True)
    grp          = Column(String(64),  default="auto")
    ip           = Column(String(45),  unique=True, index=True, nullable=False)
    mac          = Column(String(32),  unique=True, nullable=True, default=None)
    user         = Column(String(64),  default="")
    passwd       = Column(String(64),  default="")
    status       = Column(String(32),  default="unknown")
    lastSeen     = Column(BigInteger,  default=0)
    sim1number   = Column(String(32),  default="")
    sim1operator = Column(String(64),  default="")
    sim1signal   = Column(Integer,     default=0)
    sim2number   = Column(String(32),  default="")
    sim2operator = Column(String(64),  default="")
    sim2signal   = Column(Integer,     default=0)
    token        = Column(Text,        default="")
    # FIX(N3): dedicated column for firmware version so OTA check never
    # overwrites the device's stable identifier (devId).
    firmware_version = Column(String(64), default="")
    alias        = Column(String(128), default="")
    # Manual ordering for the dashboard list (lower first). 0 means "not
    # ordered yet", which keeps the historical newest-first ordering until the
    # operator drags a card.
    sort_order   = Column(Integer,     default=0)
    # Free-form operator note (carrier plan, monthly fee, spare SIM, ...).
    note         = Column(Text,        default="")
    created      = Column(String(32),  default="")


# FIX(P0#2): persist auth tokens to SQLite so that multiple uvicorn processes
# (e.g. the v4 and v6 listeners) share the same session store.
class AuthToken(Base):
    __tablename__ = "auth_tokens"
    token    = Column(String(128), primary_key=True)
    username = Column(String(64),  default="")
    exp      = Column(BigInteger,  default=0, index=True)


# FIX(M3/H2): rate-limit events live in SQLite, mirroring the AuthToken store,
# so the v4 + v6 uvicorn processes share a single limit budget instead of each
# keeping its own in-memory window (which doubled every effective limit). This
# also fixes the in-memory unbounded-key growth (H2): expired rows are pruned
# on every touch plus a periodic sweep, so the table stays bounded by the
# distinct keys seen inside the active window.
class RateEvent(Base):
    __tablename__ = "rate_events"
    id    = Column(Integer, primary_key=True, autoincrement=True)
    scope = Column(String(32),  index=True, nullable=False)
    rkey  = Column(String(160), index=True, nullable=False)
    ts    = Column(Integer,     index=True, nullable=False)


# Runtime settings (currently the board admin credential). Kept in SQLite so
# the v4 and v6 uvicorn processes agree and a restart keeps the value, and so
# the UI can set it once instead of every code path hard-coding DEFAULTPASS.
class Setting(Base):
    __tablename__ = "settings"
    key   = Column(String(64), primary_key=True)
    value = Column(Text, default="")


# SMS/call history pulled from the boards or pushed to /webhook. The old
# lvyou-panel kept these in its own SQLite file; they live here so the whole
# fleet is visible from one place and survives restarts.
class SmsRecord(Base):
    __tablename__ = "sms_records"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    device_id  = Column(Integer, index=True, nullable=True)
    dev_id     = Column(String(128), index=True, default="")
    sim_slot   = Column(String(16), default="")
    phone      = Column(String(64), default="", index=True)
    content    = Column(Text, default="")
    direction  = Column(String(16), default="received", index=True)
    raw_json   = Column(Text, default="")
    sms_ts     = Column(BigInteger, default=0, index=True)
    sms_time   = Column(String(32), default="")
    created    = Column(String(32), default="")
    # sha1(device|ts|slot|direction|phone|content): INSERT OR IGNORE makes
    # polling and webhook delivery idempotent even when both arrive for the
    # same message.
    dedup      = Column(String(40), unique=True, index=True)


class CallRecord(Base):
    __tablename__ = "call_records"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    device_id  = Column(Integer, index=True, nullable=True)
    dev_id     = Column(String(128), index=True, default="")
    sim_slot   = Column(String(16), default="")
    phone      = Column(String(64), default="", index=True)
    direction  = Column(String(16), default="incoming", index=True)
    action     = Column(String(16), default="")
    duration   = Column(Integer, default=0)
    raw_json   = Column(Text, default="")
    call_time  = Column(String(32), default="")
    start_ts   = Column(BigInteger, default=0, index=True)
    created    = Column(String(32), default="")
    dedup      = Column(String(40), unique=True, index=True)


class SmsJob(Base):
    """Track one sendsms command from local queue to the board's final report.

    The board accepts sendsms asynchronously: the HTTP response is only the
    type=401 acknowledgement, while the real result arrives later as type=402
    (and type=502 when it reached the carrier). Keeping the job keyed by tid
    lets the UI show that distinction instead of treating "queued" as "sent".
    """
    __tablename__ = "sms_jobs"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    tid         = Column(String(64), unique=True, index=True, nullable=False)
    device_id   = Column(Integer, index=True, nullable=True)
    dev_id      = Column(String(128), index=True, default="")
    slot        = Column(Integer, default=1)
    phone       = Column(String(64), default="")
    content     = Column(Text, default="")
    status      = Column(String(16), default="queued", index=True)
    delivery_id = Column(String(128), default="")
    note        = Column(String(255), default="")
    created     = Column(BigInteger, default=0, index=True)
    updated     = Column(BigInteger, default=0)


def _create_all_with_retry(attempts: int = 6) -> None:
    """Create tables, tolerating the v4/v6 startup race.

    The entrypoint starts two uvicorn processes that both import this module.
    When a new table is introduced they can run CREATE TABLE at the same
    instant and the loser dies with "table ... already exists" -- which is
    exactly what happened when the settings table was added.
    """
    for attempt in range(attempts):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except OperationalError as exc:
            if "already exists" not in str(exc) or attempt == attempts - 1:
                raise
            time.sleep(0.2 * (attempt + 1))


_create_all_with_retry()


def run_migrations() -> None:
    """Idempotent ALTER TABLE migrations for columns added across versions."""
    alters = [
        ("devices", "token",            "TEXT DEFAULT ''"),
        ("devices", "sim1signal",       "INTEGER DEFAULT 0"),
        ("devices", "sim2signal",       "INTEGER DEFAULT 0"),
        ("devices", "firmware_version", "VARCHAR(64) DEFAULT ''"),
        ("devices", "sort_order",       "INTEGER DEFAULT 0"),
        ("devices", "note",             "TEXT DEFAULT ''"),
    ]
    with engine.connect() as conn:
        for table, col, coltype in alters:
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            cols = [r[1] for r in rows]
            if col not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))
        conn.execute(text("UPDATE devices SET mac = NULL WHERE mac = ''"))
        conn.commit()


run_migrations()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def nowts() -> int:
    return int(time.time())


# ── Token persistence (SQLite-backed, shared across processes) ───────────────
def cleanup_expired_tokens() -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM auth_tokens WHERE exp <= :n"), {"n": nowts()})
    except Exception:
        logger.debug("token cleanup failed", exc_info=True)


def get_token_record(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT username, exp FROM auth_tokens WHERE token = :t"),
                {"t": token},
            ).first()
            if not row:
                return None
            return {"username": row[0] or "", "exp": int(row[1] or 0)}
    except Exception:
        logger.debug("token lookup failed", exc_info=True)
        return None


def insert_token(token: str, username: str, exp: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT OR REPLACE INTO auth_tokens(token, username, exp) VALUES(:t, :u, :e)"),
            {"t": token, "u": username, "e": exp},
        )


def delete_token(token: str) -> None:
    if not token:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM auth_tokens WHERE token = :t"), {"t": token})
    except Exception:
        logger.debug("token delete failed", exc_info=True)


def extend_token(token: str, exp: int) -> None:
    """Push a live token's expiry forward (sliding session)."""
    if not token:
        return
    try:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE auth_tokens SET exp = :e WHERE token = :t"),
                {"e": int(exp), "t": token},
            )
    except Exception:
        logger.debug("token extend failed", exc_info=True)


def issue_token(username: str) -> str:
    cleanup_expired_tokens()
    token = secrets.token_urlsafe(32)
    insert_token(token, username, nowts() + TOKEN_TTL_SECONDS)
    return token


# ── Rate-limit event store (SQLite-backed, shared across processes) ──────────
def rate_count(scope: str, key: str, period: float) -> int:
    """Count events for (scope, key) inside the trailing `period` seconds,
    pruning anything older in the same statement window."""
    cutoff = nowts() - int(period)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM rate_events WHERE scope=:s AND rkey=:k AND ts < :c"),
                {"s": scope, "k": key, "c": cutoff},
            )
            row = conn.execute(
                text("SELECT COUNT(*) FROM rate_events WHERE scope=:s AND rkey=:k"),
                {"s": scope, "k": key},
            ).first()
            return int(row[0] or 0) if row else 0
    except Exception:
        logger.debug("rate_count failed", exc_info=True)
        return 0


def rate_add(scope: str, key: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO rate_events(scope, rkey, ts) VALUES(:s, :k, :t)"),
                {"s": scope, "k": key, "t": nowts()},
            )
    except Exception:
        logger.debug("rate_add failed", exc_info=True)


def rate_reset(scope: str, key: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM rate_events WHERE scope=:s AND rkey=:k"),
                {"s": scope, "k": key},
            )
    except Exception:
        logger.debug("rate_reset failed", exc_info=True)


def cleanup_rate_events(max_age: int) -> None:
    """Periodic sweep: drop any event older than the widest configured window
    so keys that go quiet do not leave rows behind (H2)."""
    try:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM rate_events WHERE ts < :c"),
                {"c": nowts() - int(max_age)},
            )
    except Exception:
        logger.debug("rate_events cleanup failed", exc_info=True)
