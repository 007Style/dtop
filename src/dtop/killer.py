"""Hard-kill helper (SIGKILL on POSIX, TerminateProcess on Windows)."""
from __future__ import annotations

import os

import psutil


def kill_pid(pid: int) -> tuple[bool, str]:
    """Immediately kill ``pid``. Returns ``(ok, message)``."""
    if pid <= 1:
        return False, f"refusing to kill pid {pid}"
    if pid == os.getpid():
        return False, "refusing to kill dtop itself (press q to quit)"
    try:
        p = psutil.Process(pid)
        name = p.name()
        p.kill()
        try:
            p.wait(timeout=1.5)
        except psutil.TimeoutExpired:
            return False, f"sent kill to {name} ({pid}) but it is still running"
        return True, f"killed {name} ({pid})"
    except psutil.NoSuchProcess:
        return True, f"pid {pid} already gone"
    except psutil.AccessDenied:
        hint = "run as Administrator" if os.name == "nt" else "re-run dtop with sudo"
        return False, f"access denied killing pid {pid} - {hint}"
    except psutil.Error as exc:
        return False, f"could not kill pid {pid}: {exc}"
