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
    text,
)
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
    created      = Column(String(32),  default="")


# FIX(P0#2): persist auth tokens to SQLite so that multiple uvicorn processes
# (e.g. the v4 and v6 listeners) share the same session store.
class AuthToken(Base):
    __tablename__ = "auth_tokens"
    token    = Column(String(128), primary_key=True)
    username = Column(String(64),  default="")
    exp      = Column(BigInteger,  default=0, index=True)


Base.metadata.create_all(bind=engine)


def run_migrations() -> None:
    """Idempotent ALTER TABLE migrations for columns added across versions."""
    alters = [
        ("devices", "token",            "TEXT DEFAULT ''"),
        ("devices", "sim1signal",       "INTEGER DEFAULT 0"),
        ("devices", "sim2signal",       "INTEGER DEFAULT 0"),
        ("devices", "firmware_version", "VARCHAR(64) DEFAULT ''"),
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


def issue_token(username: str) -> str:
    cleanup_expired_tokens()
    token = secrets.token_urlsafe(32)
    insert_token(token, username, nowts() + TOKEN_TTL_SECONDS)
    return token
