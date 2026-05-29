"""Network-level security helpers: SSRF allowlist, client IP, prewarm.

FIX(P2#4): extracted from ``backend/main.py``. These helpers are pure
host-OS / network primitives -- no FastAPI app reference, no DB call --
which is exactly what we want in the leaf layer of the import graph.

Anything that needs to raise an HTTP error (e.g. ``ensure_device_ip_allowed``)
returns a tuple or raises a plain RuntimeError; the caller in main.py
translates that into HTTPException so this module can be unit-tested
without FastAPI installed.
"""

from __future__ import annotations

import logging
import re
import socket
import subprocess
import threading
import time
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    ip_address,
    ip_network,
)
from itertools import islice
from threading import Lock as _Lock
from typing import Dict, List, Tuple

from backend.config import (
    CIDRFALLBACKLIMIT,
    COOKIE_SAMESITE,
    COOKIE_SECURE,
    LOCAL_NETS_CACHE_TTL,
    PREWARM_CONCURRENCY,
    TCP_TIMEOUT,
    TRUSTED_PROXY_HOPS,
    UIPASS,
)

logger = logging.getLogger("board-manager")


def env_truthy(name: str) -> bool:
    import os
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


# FIX(P0#5): never invoke a shell. Each helper below feeds argv directly to
# subprocess; no user-controllable values are passed.
def run_cmd(argv: List[str], timeout: float = 3.0) -> str:
    return subprocess.check_output(
        argv, stderr=subprocess.DEVNULL, text=True, timeout=timeout
    ).strip()


def guess_ipv4_cidr() -> str:
    try:
        route_text = run_cmd(["ip", "-4", "route", "show", "default"])
        for line in route_text.splitlines():
            match = re.search(r"dev\s+(\S+)", line)
            if not match:
                continue
            iface = match.group(1)
            addr_text = run_cmd(["ip", "-4", "addr", "show", "dev", iface])
            for addr_line in addr_text.splitlines():
                m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+/\d+)", addr_line)
                if m:
                    net = ip_network(m.group(1), strict=False)
                    if isinstance(net, IPv4Network):
                        return f"{net.network_address}/{net.prefixlen}"
            break
    except Exception:
        # FIX(P2#12): debug-level so an operator running with -L debug
        # can see why guess_ipv4_cidr fell through to the second strategy.
        logger.debug("default-route CIDR detection failed", exc_info=True)
    try:
        txt = run_cmd(["ip", "-o", "-4", "addr", "show"])
        for line in txt.splitlines():
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            iface, cidr = parts[1], parts[3]
            if iface == "lo":
                continue
            net = ip_network(cidr, strict=False)
            if isinstance(net, IPv4Network):
                return f"{net.network_address}/{net.prefixlen}"
    except Exception:
        # FIX(P2#12): same -- log so the 192.168.1.0/24 fallback is never
        # silent. Operators have hit "scan finds nothing" because both
        # detection paths failed and they had no way to know.
        logger.debug("addr-list CIDR detection failed", exc_info=True)
    return "192.168.1.0/24"


# FIX(P0#4): cache the local network list with a short TTL so the SSRF
# allow-list check does not fork ``ip`` for every outbound device request.
_LOCAL_NETS_CACHE: Tuple[float, List[IPv4Network]] = (0.0, [])
_LOCAL_NETS_LOCK = _Lock()


def local_ipv4_networks() -> List[IPv4Network]:
    global _LOCAL_NETS_CACHE
    now = time.time()
    cached_at, cached = _LOCAL_NETS_CACHE
    if cached and now - cached_at < LOCAL_NETS_CACHE_TTL:
        return cached
    with _LOCAL_NETS_LOCK:
        cached_at, cached = _LOCAL_NETS_CACHE
        if cached and now - cached_at < LOCAL_NETS_CACHE_TTL:
            return cached
        nets: List[IPv4Network] = []
        try:
            txt = run_cmd(["ip", "-o", "-4", "addr", "show"])
            for line in txt.splitlines():
                parts = line.strip().split()
                if len(parts) < 4:
                    continue
                iface, cidr = parts[1], parts[3]
                if iface == "lo":
                    continue
                try:
                    net = ip_network(cidr, strict=False)
                    if isinstance(net, IPv4Network):
                        nets.append(net)
                except Exception:
                    continue
        except Exception as exc:
            # FIX(P2#12): the silent fallback used to make mis-configured
            # bridges look like "no local nets" with no diagnostic. Log
            # at warning level so an operator sees why SSRF layer-2 is
            # disabled.
            logger.warning("local IPv4 network discovery failed: %r", exc)
        _LOCAL_NETS_CACHE = (now, nets)
        return nets


def is_device_ip_allowed(ip: str) -> bool:
    """FIX(P0#4): SSRF allow-list. Two layers:
      1. Must be RFC1918 private / IPv6 ULA, never loopback/link-local/etc.
      2. Must fall inside a network the host itself is attached to (so a
         10.0.0.0/8 device cannot be scanned from a 192.168.x host).

    Layer 2 is best-effort: when the host has no IPv4 networks (e.g. test
    environment) it is skipped rather than failing closed, which would
    block legitimate scans before any network is configured.
    """
    try:
        addr = ip_address(ip)
    except Exception:
        return False
    if isinstance(addr, IPv4Address):
        if (addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_unspecified or addr.is_reserved):
            return False
        if not addr.is_private:
            return False
        nets = local_ipv4_networks()
        if not nets:
            return True
        for net in nets:
            if addr in net:
                return True
        return False
    if isinstance(addr, IPv6Address):
        if (addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_unspecified or addr.is_reserved):
            return False
        return addr.is_private or addr.is_site_local
    return False


def get_arp_table() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open("/proc/net/arp") as handle:
            for line in handle.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 4:
                    ip = parts[0].strip()
                    mac = parts[3].strip().upper()
                    if mac and mac != "00:00:00:00:00:00" and ":" in mac:
                        out[ip] = mac
    except Exception:
        pass
    try:
        txt = subprocess.check_output(
            ["ip", "neigh", "show"], text=True, stderr=subprocess.DEVNULL
        )
        for line in txt.splitlines():
            parts = line.split()
            if len(parts) >= 5 and "lladdr" in parts:
                ip = parts[0].strip()
                mac = parts[parts.index("lladdr") + 1].strip().upper()
                if mac and mac != "00:00:00:00:00:00" and ":" in mac:
                    out[ip] = mac
    except Exception:
        pass
    return out


# FIX(P1#11): cap concurrent ping subprocesses, avoiding 1024 fork()s at once.
def prewarm_neighbors(net: IPv4Network) -> None:
    """Ping potential hosts to populate the ARP cache before scanning.

    Uses the shared ThreadPoolExecutor instead of creating one thread per
    host. The executor's worker pool naturally caps concurrency, avoiding
    the overhead of spawning/joining up to 1024 daemon threads.
    """
    try:
        hosts = [str(host) for host in islice(net.hosts(), CIDRFALLBACKLIMIT)]
        deadline = time.time() + 8

        def _ping(ip_str: str) -> None:
            if time.time() >= deadline:
                return
            try:
                subprocess.run(
                    ["ping", "-c", "1", "-W", "1", ip_str],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=max(0.5, deadline - time.time()),
                )
            except Exception:
                pass

        # Import here to avoid circular dependency; the executor is
        # created at process startup by main.py lifespan.
        from backend.security import _shared_executor_ref
        executor = _shared_executor_ref()
        if executor:
            list(executor.map(_ping, hosts))
        else:
            # Fallback: sequential (should not happen in production)
            for h in hosts:
                _ping(h)
        time.sleep(0.2)
    except Exception:
        logger.debug("prewarm_neighbors failed", exc_info=True)


# Module-level reference to the shared executor, set by main.py at startup.
_SHARED_EXECUTOR = None

def _shared_executor_ref():
    return _SHARED_EXECUTOR

def set_shared_executor(executor):
    global _SHARED_EXECUTOR
    _SHARED_EXECUTOR = executor


def tcp_port_open(ip: str, port: int = 80) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=TCP_TIMEOUT):
            return True
    except Exception:
        return False


def client_ip_from_request(request) -> str:
    """Resolve real client IP honouring X-Forwarded-For when behind a trusted
    reverse proxy. Set BMTRUSTEDPROXYHOPS >= 1 to read the header.

    `request` is a starlette Request; we use duck-typing rather than importing
    fastapi here so the helper stays test-friendly."""
    if TRUSTED_PROXY_HOPS > 0:
        xff = (request.headers.get("x-forwarded-for", "")
               or request.headers.get("X-Forwarded-For", ""))
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts:
                idx = max(0, len(parts) - TRUSTED_PROXY_HOPS)
                return parts[idx]
        real = (request.headers.get("x-real-ip", "")
                or request.headers.get("X-Real-IP", ""))
        if real:
            return real.strip()
    return request.client.host if request.client else "unknown"


def validate_startup_security() -> None:
    """FIX(P0#1): refuse to start with an empty or default BMUIPASS unless the
    operator explicitly opts into the insecure default for local development.

    This guard is what makes the documented ``BMUIPASS`` requirement actually
    enforceable -- without it, an unset env var silently falls back to
    admin/admin, which is exactly the regression that prompted this fix."""
    if (not UIPASS or UIPASS == "admin") and not env_truthy("BMINSECURE_DEFAULT_PASSWORD"):
        raise RuntimeError(
            "BMUIPASS must be set to a strong non-default password before starting. "
            "Set BMUIPASS=<strong password> (or BMINSECURE_DEFAULT_PASSWORD=1 for "
            "local development only)."
        )
    # FIX(P2#1): SameSite=None is rejected by every modern browser unless
    # the cookie is also Secure. Catch this misconfiguration at startup
    # rather than silently dropping the auth cookie at request time.
    if COOKIE_SAMESITE == "none" and not COOKIE_SECURE:
        raise RuntimeError(
            "BMCOOKIESAMESITE=none requires BMCOOKIESECURE=1; browsers will "
            "reject SameSite=None cookies sent over plain HTTP."
        )
