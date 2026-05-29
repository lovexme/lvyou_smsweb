"""Network scan routes.

Extracted from backend/main.py.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid as _uuid
from ipaddress import IPv4Network, ip_address, ip_network
from itertools import islice
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from backend.config import (
    CIDRFALLBACKLIMIT,
    DEFAULTPASS,
    DEFAULTUSER,
    SCAN_TTL,
)
from backend.db import SessionLocal

logger = logging.getLogger("board-manager")

router = APIRouter()

# Injected from main.py
_guessipv4cidr = None
_prewarm_neighbors = None
_getarptable = None
_tcp_port_open = None
_get_shared_executor = None
_istargetdevice = None
_ensure_device_ip_allowed_raise = None
_upsertdevice = None
_audit = None


def inject(
    *,
    guessipv4cidr=None,
    prewarm_neighbors=None,
    getarptable=None,
    tcp_port_open=None,
    get_shared_executor=None,
    is_target_device=None,
    ensure_device_ip_allowed_raise=None,
    upsert_device=None,
    audit=None,
):
    global _guessipv4cidr, _prewarm_neighbors, _getarptable
    global _tcp_port_open, _get_shared_executor, _istargetdevice
    global _ensure_device_ip_allowed_raise, _upsertdevice, _audit
    if guessipv4cidr: _guessipv4cidr = guessipv4cidr
    if prewarm_neighbors: _prewarm_neighbors = prewarm_neighbors
    if getarptable: _getarptable = getarptable
    if tcp_port_open: _tcp_port_open = tcp_port_open
    if get_shared_executor: _get_shared_executor = get_shared_executor
    if is_target_device: _istargetdevice = is_target_device
    if ensure_device_ip_allowed_raise: _ensure_device_ip_allowed_raise = ensure_device_ip_allowed_raise
    if upsert_device: _upsertdevice = upsert_device
    if audit: _audit = audit


class ScanStartReq(BaseModel):
    cidr: Optional[str] = None
    group: Optional[str] = None
    user: str = ""
    password: str = ""


class ScanState:
    def __init__(self):
        self.status = "pending"
        self.progress = ""
        self.results: List[Dict[str, Any]] = []
        self.found = 0
        self.scanned = 0
        self.total_ips = 0
        self.cidr = ""
        self.finished_at: float = 0.0
        self._lock = threading.Lock()

    def set_status(self, status: str, progress: Optional[str] = None) -> None:
        with self._lock:
            self.status = status
            if progress is not None:
                self.progress = progress

    def set_progress(self, progress: str) -> None:
        with self._lock:
            self.progress = progress

    def set_counts(self, *, scanned: Optional[int] = None, found: Optional[int] = None, total_ips: Optional[int] = None) -> None:
        with self._lock:
            if scanned is not None: self.scanned = scanned
            if found is not None: self.found = found
            if total_ips is not None: self.total_ips = total_ips

    def set_results(self, results: List[Dict[str, Any]]) -> None:
        with self._lock:
            self.results = results
            self.found = len(results)

    def set_cidr(self, cidr: str) -> None:
        with self._lock:
            self.cidr = cidr

    def mark_done(self) -> None:
        with self._lock:
            self.finished_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "status": self.status, "progress": self.progress,
                "found": self.found, "scanned": self.scanned,
                "total_ips": self.total_ips, "cidr": self.cidr,
                "devices": [{"ip": r["ip"], "devId": r.get("devId", "")} for r in self.results],
            }


_active_scans: Dict[str, ScanState] = {}
_active_scans_lock = threading.Lock()


def _cleanup_old_scans() -> None:
    now = time.time()
    with _active_scans_lock:
        expired = [sid for sid, st in _active_scans.items()
                   if st.finished_at > 0 and now - st.finished_at > SCAN_TTL]
        for sid in expired:
            _active_scans.pop(sid, None)


def _safe_ip_in_net(ip: str, net: IPv4Network) -> bool:
    try:
        return ip_address(ip) in net
    except Exception:
        return False


def _scan_worker(cidr: str, group: Optional[str], user: str, password: str, state: ScanState):
    try:
        state.set_status("scanning", "解析网段...")
        net = ip_network(cidr, strict=False)

        state.set_progress("预热邻居...")
        _prewarm_neighbors(net)
        arptable = _getarptable()

        seen: set = set()
        iplist: List[str] = []
        arp_ips = [ip for ip in arptable if _safe_ip_in_net(ip, net)]
        for ip in arp_ips + [str(host) for host in islice(net.hosts(), CIDRFALLBACKLIMIT)]:
            if ip not in seen:
                seen.add(ip)
                iplist.append(ip)
            if len(iplist) >= CIDRFALLBACKLIMIT:
                break
        state.set_counts(total_ips=len(iplist))
        state.set_progress(f"TCP 探测 {len(iplist)} 个 IP...")

        open80: List[str] = []
        olock = threading.Lock()

        def _tcp(ip: str):
            if _tcp_port_open(ip, 80):
                with olock:
                    open80.append(ip)

        executor = _get_shared_executor()
        list(executor.map(_tcp, iplist))
        state.set_counts(scanned=len(open80))
        state.set_progress(f"验证 {len(open80)} 台设备...")

        found: List[Dict[str, Any]] = []
        flock = threading.Lock()

        def _probe(ip: str):
            try:
                ok, _ = _istargetdevice(ip, user, password)
            except HTTPException:
                return
            if ok:
                mac = arptable.get(ip, "")
                with flock:
                    found.append({"ip": ip, "mac": mac, "devId": "", "grp": group})

        list(executor.map(_probe, open80))

        state.set_results(found)
        state.set_progress(f"完成，发现 {len(found)} 台")
    except Exception as exc:
        state.set_status("error", f"扫描出错: {exc}")
        logger.error("scan error for %s: %s", cidr, exc, exc_info=True)
        return
    state.set_status("done")


def _run_scan_bg(scan_id: str, cidr: str, group: Optional[str], user: str, password: str):
    with _active_scans_lock:
        state = _active_scans.get(scan_id)
    if not state:
        return

    try:
        _scan_worker(cidr, group, user, password, state)

        if state.status == "error":
            state.mark_done()
            return

        state.set_status("saving", "保存设备到数据库...")
        db = SessionLocal()
        try:
            saved: List[Dict[str, Any]] = []
            failed: List[Dict[str, Any]] = []
            for item in state.results:
                try:
                    d = _upsertdevice(db, item["ip"], item["mac"], DEFAULTUSER, DEFAULTPASS, item.get("grp"))
                    if d.get("error"):
                        failed.append(d)
                    else:
                        saved.append(d)
                except Exception as exc:
                    failed.append({"ip": item["ip"], "error": str(exc)})
                    logger.warning("save device %s failed: %s", item["ip"], exc)
            state.set_results(saved)
            if failed:
                failed_text = "；".join(f"{x.get('ip', '')}: {x.get('error', '')}" for x in failed[:3])
                state.set_status("error", f"发现 {len(saved) + len(failed)} 台，成功添加 {len(saved)} 台，失败 {len(failed)} 台：{failed_text}")
            else:
                state.set_status("done", f"完成，发现 {len(saved)} 台设备")
        finally:
            db.close()
    except Exception as exc:
        state.set_status("error", f"保存失败: {exc}")
        logger.error("scan save error: %s", exc, exc_info=True)
    finally:
        state.mark_done()


def _submit_scan(cidr: Optional[str], group: Optional[str], user: str, password: str, background_tasks: BackgroundTasks) -> str:
    if not cidr:
        cidr = _guessipv4cidr()
    try:
        net = ip_network(cidr, strict=False)
        if not isinstance(net, IPv4Network):
            raise ValueError("only IPv4 supported")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无效的网段: {exc}")
    scan_id = _uuid.uuid4().hex[:12]
    state = ScanState()
    state.set_cidr(cidr)
    with _active_scans_lock:
        _active_scans[scan_id] = state
    background_tasks.add_task(_run_scan_bg, scan_id, cidr, group, user, password)
    _audit("scan_start", detail=f"cidr={cidr}")
    return scan_id


@router.post("/api/scan/start")
def scanstart(req: ScanStartReq, background_tasks: BackgroundTasks):
    user = req.user.strip() or DEFAULTUSER
    password = req.password.strip() or DEFAULTPASS
    scan_id = _submit_scan(req.cidr, req.group, user, password, background_tasks)
    return {"ok": True, "scanId": scan_id}


@router.get("/api/scan/status/{scan_id}")
def scanstatus(scan_id: str):
    _cleanup_old_scans()
    with _active_scans_lock:
        state = _active_scans.get(scan_id)
    if not state:
        raise HTTPException(status_code=404, detail="扫描任务不存在")
    return state.to_dict()
