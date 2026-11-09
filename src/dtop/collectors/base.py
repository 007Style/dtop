"""Collector base + shared helpers (interfaces, IP->interface mapping)."""
from __future__ import annotations

import ipaddress
import socket
import time

import psutil

from dtop.models import IfaceCounters, Snapshot


class CollectorError(RuntimeError):
    pass


class Collector:
    """Produces a :class:`Snapshot` each time :meth:`sample` is called."""

    name = "base"
    description = ""
    per_process_bytes = True
    synthetic = False  # True => PIDs are fake: never look them up or kill them

    def sample(self) -> Snapshot:  # pragma: no cover - interface
        raise NotImplementedError

    def doctor(self) -> list[str]:
        return [f"collector: {self.name} - {self.description}"]


def read_ifaces() -> dict[str, IfaceCounters]:
    """Interface counters + addresses via psutil (identical on every OS)."""
    out: dict[str, IfaceCounters] = {}
    try:
        io = psutil.net_io_counters(pernic=True)
    except Exception:
        io = {}
    try:
        stats = psutil.net_if_stats()
    except Exception:
        stats = {}
    try:
        addrs = psutil.net_if_addrs()
    except Exception:
        addrs = {}
    for name, c in io.items():
        st = stats.get(name)
        ic = IfaceCounters(
            name=name, bytes_in=c.bytes_recv, bytes_out=c.bytes_sent,
            pkts_in=c.packets_recv, pkts_out=c.packets_sent,
            errs=c.errin + c.errout, drops=c.dropin + c.dropout,
            is_up=bool(st.isup) if st else True,
            speed=int(st.speed) if st else 0, mtu=int(st.mtu) if st else 0,
        )
        for a in addrs.get(name, []):
            if a.family == socket.AF_INET:
                ic.ipv4.append(a.address)
            elif a.family == socket.AF_INET6:
                ic.ipv6.append(a.address.split("%")[0])
            elif getattr(psutil, "AF_LINK", None) is not None and a.family == psutil.AF_LINK:
                ic.mac = a.address
        out[name] = ic
    return out


def ip_to_iface_map(ifaces: dict[str, IfaceCounters]) -> dict[str, str]:
    m: dict[str, str] = {}
    for n, ic in ifaces.items():
        for ip in ic.ipv4 + ic.ipv6:
            m[ip] = n
    return m


def guess_iface(local_ip: str, remote_ip: str, ipmap: dict[str, str]) -> str:
    """Best-effort interface for a socket from its local (or loopback) address."""
    ip = local_ip.split("%")[0]
    if ip in ipmap:
        return ipmap[ip]
    try:
        if ipaddress.ip_address(remote_ip.split("%")[0]).is_loopback:
            for n in ipmap.values():
                if n.startswith("lo"):
                    return n
            return "lo"
    except ValueError:
        pass
    return ""


def now() -> float:
    return time.time()
