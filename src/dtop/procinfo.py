"""Process metadata lookups (psutil), cached and failure-tolerant."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import psutil


@dataclass
class ProcInfo:
    pid: int
    name: str = ""
    exe: str = ""
    cmdline: str = ""
    user: str = ""
    ppid: int = 0
    parent_name: str = ""
    create_time: float = 0.0
    status: str = ""
    threads: int = 0
    thread_list: list[str] = field(default_factory=list)
    cpu: float = 0.0
    rss: int = 0
    cwd: str = ""
    open_files: int = 0
    conns: list[str] = field(default_factory=list)
    alive: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def uptime(self) -> float | None:
        return (time.time() - self.create_time) if self.create_time else None


def _try(fn, default, notes: list[str], label: str):
    try:
        return fn()
    except psutil.AccessDenied:
        notes.append(f"{label}: access denied (run as root/admin)")
    except psutil.NoSuchProcess:
        raise
    except (psutil.Error, OSError, ValueError):
        pass
    return default


def get_info(pid: int, full: bool = True) -> ProcInfo | None:
    """Collect process details. Returns None if the process does not exist."""
    info = ProcInfo(pid=pid)
    try:
        p = psutil.Process(pid)
        n = info.notes
        info.name = _try(p.name, "", n, "name")
        info.exe = _try(p.exe, "", n, "exe")
        info.user = _try(p.username, "", n, "user")
        info.create_time = _try(p.create_time, 0.0, n, "start time")
        if not full:
            return info
        info.cmdline = " ".join(_try(p.cmdline, [], n, "cmdline"))
        info.ppid = _try(p.ppid, 0, n, "ppid")
        if info.ppid:
            try:
                info.parent_name = psutil.Process(info.ppid).name()
            except psutil.Error:
                info.parent_name = "?"
        info.status = _try(p.status, "", n, "status")
        info.threads = _try(p.num_threads, 0, n, "threads")
        info.thread_list = [
            f"{t.id} (cpu {t.user_time + t.system_time:.1f}s)"
            for t in _try(p.threads, [], n, "thread list")[:8]
        ]
        info.cpu = _try(lambda: p.cpu_percent(interval=None), 0.0, n, "cpu")
        mem = _try(p.memory_info, None, n, "memory")
        info.rss = getattr(mem, "rss", 0) if mem else 0
        info.cwd = _try(p.cwd, "", n, "cwd")
        info.open_files = len(_try(p.open_files, [], n, "open files"))
        getter = getattr(p, "net_connections", None) or p.connections
        for c in _try(lambda: getter(kind="inet"), [], n, "connections"):
            laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "*"
            raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "*"
            info.conns.append(f"{laddr} -> {raddr} {c.status}")
    except psutil.NoSuchProcess:
        return None
    return info


class InfoCache:
    """Light per-pid cache keyed on (pid, name); static facts only."""

    def __init__(self) -> None:
        self._d: dict[tuple[int, str], ProcInfo | None] = {}

    def light(self, pid: int, name: str) -> ProcInfo | None:
        k = (pid, name)
        if k not in self._d:
            self._d[k] = get_info(pid, full=False)
        return self._d[k]
