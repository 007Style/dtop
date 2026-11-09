"""Platform-neutral data containers passed from collectors to the engine."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Flow:
    """One socket/flow owned by a process, with cumulative byte counters.

    ``bytes_in``/``bytes_out`` are ``None`` on platforms that cannot report
    per-process byte counters (the engine then falls back to detecting *new*
    connections as activity).
    """

    pid: int
    name: str
    proto: str
    local: str
    remote: str
    iface: str
    state: str
    bytes_in: int | None
    bytes_out: int | None

    @property
    def key(self) -> tuple[int, str, str, str, str]:
        return (self.pid, self.proto, self.local, self.remote, self.iface)


@dataclass
class IfaceCounters:
    """Cumulative counters + static info for one network interface."""

    name: str
    bytes_in: int = 0
    bytes_out: int = 0
    pkts_in: int = 0
    pkts_out: int = 0
    errs: int = 0
    drops: int = 0
    is_up: bool = True
    speed: int = 0  # Mbit/s, 0 = unknown
    mtu: int = 0
    ipv4: list[str] = field(default_factory=list)
    ipv6: list[str] = field(default_factory=list)
    mac: str = ""


@dataclass
class Snapshot:
    ts: float
    flows: list[Flow]
    ifaces: dict[str, IfaceCounters]
    has_bytes: bool = True
    notes: list[str] = field(default_factory=list)
