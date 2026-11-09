"""Heuristic 'is this suspicious?' score for an infrequent talker.

This is a triage aid, not a verdict. Every point is explained in ``reasons``.
"""
from __future__ import annotations

import ipaddress
import re

from dtop.fmt import split_endpoint
from dtop.procinfo import ProcInfo

SHELLS_AND_INTERPRETERS = {
    "bash", "sh", "zsh", "dash", "fish", "nc", "ncat", "netcat", "socat",
    "python", "python3", "perl", "ruby", "php", "node", "powershell",
    "pwsh", "cmd", "wscript", "cscript", "mshta", "rundll32", "regsvr32",
}

_SUSPICIOUS_PATH = re.compile(
    r"(^|[\\/])(tmp|temp|downloads|\.cache|appdata[\\/]local[\\/]temp|"
    r"\$recycle\.bin|shm)([\\/]|$)|[\\/]\.[^\\/.][^\\/]*[\\/]",
    re.IGNORECASE,
)


def is_public(endpoint: str) -> bool:
    host, _ = split_endpoint(endpoint)
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    return ip.is_global and not ip.is_multicast


def level(score: int) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 45:
        return "MED"
    if score >= 20:
        return "LOW"
    return "OK"


def assess(
    *,
    name: str,
    pattern: str,
    remote: str,
    total_in: int,
    total_out: int,
    bytes_known: bool,
    info: ProcInfo | None,
) -> tuple[int, list[str]]:
    score = 0
    why: list[str] = []

    if pattern.startswith("periodic"):
        score += 25
        why.append("regular, clock-like cadence (beacon-like)")
    if pattern == "silent":
        return 0, []  # never communicated: an open socket alone is not evidence

    if remote and is_public(remote):
        score += 20
        why.append("talks to a public internet address")
    if bytes_known and total_out >= 100_000 and total_out > 4 * max(total_in, 1):
        score += 20
        why.append("outbound-heavy (upload >> download)")

    base = (name or "").lower().removesuffix(".exe")
    if base in SHELLS_AND_INTERPRETERS:
        score += 20
        why.append("shell/interpreter with network activity")

    if info is not None:
        if info.exe and _SUSPICIOUS_PATH.search(info.exe):
            score += 20
            why.append(f"runs from a temp/hidden/download path: {info.exe}")
        if not info.exe and info.user and "access denied" not in " ".join(info.notes):
            score += 10
            why.append("executable path unavailable")
    return min(score, 100), why
