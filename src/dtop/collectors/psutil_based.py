"""psutil-based collectors: Linux (adds ``ss`` byte counters) and generic (Windows)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess

import psutil

from dtop.collectors.base import Collector, guess_iface, ip_to_iface_map, now, read_ifaces
from dtop.models import Flow, Snapshot

_SS_USER = re.compile(r'\("([^"]*)",pid=(\d+)')
_SS_IN = re.compile(r"\bbytes_received:(\d+)")
_SS_OUT = re.compile(r"\bbytes_(?:sent|acked):(\d+)")


def parse_ss(text: str) -> dict[tuple[int, str, str], tuple[int, int]]:
    """Parse ``ss -tinpHO`` -> {(pid, local, remote): (bytes_in, bytes_out)}."""
    out: dict[tuple[int, str, str], tuple[int, int]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        m = _SS_USER.search(line)
        if not m:
            continue
        pid = int(m.group(2))
        local, remote = parts[3], parts[4]
        bin_ = _SS_IN.search(line)
        bout = re.search(r"\bbytes_sent:(\d+)", line) or _SS_OUT.search(line)
        if bin_ or bout:
            k = (pid, local, remote)
            i, o = out.get(k, (0, 0))
            out[k] = (i + int(bin_.group(1) if bin_ else 0), o + int(bout.group(1) if bout else 0))
    return out


def _name_for(pid: int, cache: dict[int, str]) -> str:
    if pid not in cache:
        try:
            cache[pid] = psutil.Process(pid).name()
        except psutil.Error:
            cache[pid] = f"pid{pid}"
    return cache[pid]


class PsutilCollector(Collector):
    """Connections via psutil. On Linux also merges ``ss`` TCP byte counters."""

    def __init__(self) -> None:
        self._names: dict[int, str] = {}
        self._ss = bool(shutil.which("ss")) and os.name == "posix"
        self.per_process_bytes = self._ss
        self.name = "linux-ss+psutil" if self._ss else "psutil-connections"
        self.description = (
            "TCP byte counters from `ss`; UDP/others inferred from new sockets"
            if self._ss else
            "per-process byte counters are not exposed by this OS; activity is inferred from "
            "new connections (use nettop on macOS, `ss` on Linux)"
        )

    def _ss_bytes(self) -> dict[tuple[int, str, str], tuple[int, int]]:
        try:
            out = subprocess.run(
                ["ss", "-tinpHO"], capture_output=True, text=True, timeout=10
            ).stdout
            return parse_ss(out)
        except (subprocess.SubprocessError, OSError):
            return {}

    def sample(self) -> Snapshot:
        ifaces = read_ifaces()
        ipmap = ip_to_iface_map(ifaces)
        sb = self._ss_bytes() if self._ss else {}
        notes: list[str] = []
        flows: list[Flow] = []
        try:
            conns = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError):
            conns = []
            notes.append("access denied listing connections - re-run with sudo/Administrator")
        skipped = 0
        for c in conns:
            if not c.pid:
                skipped += 1
                continue
            lip, lport = (c.laddr.ip, c.laddr.port) if c.laddr else ("", 0)
            rip, rport = (c.raddr.ip, c.raddr.port) if c.raddr else ("", 0)
            local = f"{lip}:{lport}"
            remote = f"{rip}:{rport}" if c.raddr else "*:*"
            proto = ("tcp" if c.type == 1 else "udp") + ("6" if ":" in lip else "4")
            b = sb.get((c.pid, local, remote))
            flows.append(Flow(
                pid=c.pid, name=_name_for(c.pid, self._names), proto=proto, local=local,
                remote=remote, iface=guess_iface(lip, rip, ipmap), state=c.status or "",
                bytes_in=b[0] if b else None, bytes_out=b[1] if b else None,
            ))
        if skipped:
            notes.append(f"{skipped} sockets have no visible owner (run as root/Administrator to see them)")
        if len(self._names) > 4096:
            self._names.clear()
        return Snapshot(ts=now(), flows=flows, ifaces=ifaces, has_bytes=self._ss, notes=notes)
