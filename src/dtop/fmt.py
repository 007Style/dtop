"""Small human-readable formatting helpers."""
from __future__ import annotations


def human_bytes(n: float | None) -> str:
    if n is None:
        return "n/a"
    if n < 1024:
        return f"{int(n)} B"
    value = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024.0 or unit == "TB":
            return f"{value:.1f} {unit}" if value < 100 else f"{value:.0f} {unit}"
    return f"{value:.0f} TB"  # pragma: no cover


def human_rate(bps: float | None) -> str:
    if bps is None:
        return "n/a"
    return human_bytes(bps) + "/s"


def human_dur(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m:02d}m"
    d, h = divmod(h, 24)
    return f"{d}d {h:02d}h"


def ago(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    if seconds < 1:
        return "now"
    return human_dur(seconds) + " ago"


def split_endpoint(endpoint: str) -> tuple[str, str]:
    """Split ``host:port`` / ``[v6]:port`` into ``(host, port)``."""
    if not endpoint:
        return "", ""
    host, sep, port = endpoint.rpartition(":")
    if not sep:
        return endpoint, ""
    return host.strip("[]"), port


_BLOCKS = " ▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int) -> str:
    """Right-aligned block sparkline of the last ``width`` values."""
    vals = list(values)[-width:]
    vals = [0.0] * (width - len(vals)) + vals
    peak = max(vals)
    if peak <= 0:
        return "▁" * width
    return "".join(_BLOCKS[max(1, int(v / peak * 8 + 0.999))] if v > 0 else "▁" for v in vals)
