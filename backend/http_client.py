"""Process-wide shared HTTP client and thread pool.

FIX(P2#4 refactor): extracted from ``backend/main.py``. The shared
``httpx.Client`` (connection-pooled, redirects disabled for SSRF safety) and
the single ``ThreadPoolExecutor`` used by every batch endpoint / scan now live
here. ``backend.device_client`` and the route modules depend on the getters
below; ``main.py``'s lifespan calls :func:`init_runtime` / :func:`shutdown_runtime`.
"""

from __future__ import annotations

import concurrent.futures
from typing import Optional

import httpx

from backend.config import CONCURRENCY, TIMEOUT
from backend.security import set_shared_executor as _set_shared_executor

_sync_client: Optional[httpx.Client] = None
_shared_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None


def _new_client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT,
        limits=httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=20),
        follow_redirects=False,
    )


def get_sync_client() -> httpx.Client:
    """Return the shared sync client; create a fresh one if the lifespan
    manager has not run yet (e.g. during tests)."""
    global _sync_client
    if _sync_client is None:
        _sync_client = _new_client()
    return _sync_client


def get_shared_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _shared_executor
    if _shared_executor is None:
        _shared_executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(CONCURRENCY, 32))
    return _shared_executor


def init_runtime() -> "tuple[httpx.Client, concurrent.futures.ThreadPoolExecutor]":
    """Create the shared client + executor and share the executor with the
    security module's neighbour-prewarm helper. Called from the app lifespan."""
    global _sync_client, _shared_executor
    _sync_client = _new_client()
    _shared_executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(CONCURRENCY, 32))
    _set_shared_executor(_shared_executor)
    return _sync_client, _shared_executor


def shutdown_runtime() -> None:
    global _sync_client, _shared_executor
    if _sync_client is not None:
        try:
            _sync_client.close()
        except Exception:
            pass
    if _shared_executor is not None:
        try:
            _shared_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
