"""Synthetic collector (--demo): realistic desultory talkers for screenshots/tests."""
from __future__ import annotations

import random

from dtop.collectors.base import Collector, now
from dtop.models import Flow, IfaceCounters, Snapshot

# name, pid, period(s), bytes in, bytes out, remote, iface, jitter
PROFILES = [
    ("softwareupdated", 612, 45, 18_000, 2_500, "17.253.144.10:443", "en0", 0.0),
    ("cloud-sync", 1204, 90, 240_000, 31_000, "52.94.236.248:443", "en0", 0.1),
    ("ntpd", 233, 64, 90, 90, "17.253.2.253:123", "en0", 0.0),
    ("zoom-updater", 3391, 150, 4_000, 900, "170.114.52.2:443", "en0", 0.3),
    ("kworker-net", 7788, 75, 600, 52_000_000, "185.220.101.47:8443", "utun3", 0.05),
    ("python3", 9120, 110, 1_200, 88_000, "45.9.148.108:4444", "en0", 0.0),
    ("mdnsd", 311, 55, 700, 400, "224.0.0.251:5353", "en0", 0.4),
    ("telemetryd", 842, 200, 9_000, 1_100, "40.126.31.71:443", "en0", 0.2),
]
CHATTY = ("browser", 4001, "142.250.80.46:443", "en0")


class DemoCollector(Collector):
    name = "demo"
    description = "synthetic data for demonstration"
    synthetic = True

    def __init__(self, seed: int = 7) -> None:
        self.rng = random.Random(seed)
        self.t0 = now()
        self._next: dict[int, float] = {}
        self._cum: dict[int, list[int]] = {}
        self._ifc: dict[str, list[int]] = {n: [0, 0] for n in ("lo0", "en0", "utun3")}
        for _, pid, period, *_ in PROFILES:
            self._next[pid] = self.t0 + self.rng.uniform(1, period)
            self._cum[pid] = [0, 0]
        self._cum[CHATTY[1]] = [0, 0]

    def sample(self) -> Snapshot:
        t = now()
        flows: list[Flow] = []
        for name, pid, period, bi, bo, remote, iface, jit in PROFILES:
            if t >= self._next[pid]:
                f = self.rng.uniform(0.6, 1.4)
                self._cum[pid][0] += int(bi * f)
                self._cum[pid][1] += int(bo * f)
                self._ifc[iface][0] += int(bi * f)
                self._ifc[iface][1] += int(bo * f)
                self._next[pid] = t + period * (1 + self.rng.uniform(-jit, jit))
            flows.append(Flow(pid, name, "tcp4", "192.168.1.20:50000", remote, iface,
                              "Established", self._cum[pid][0], self._cum[pid][1]))
        n, pid, remote, iface = CHATTY
        i, o = self.rng.randint(5_000, 90_000), self.rng.randint(1_000, 20_000)
        self._cum[pid][0] += i
        self._cum[pid][1] += o
        self._ifc[iface][0] += i
        self._ifc[iface][1] += o
        flows.append(Flow(pid, n, "tcp4", "192.168.1.20:50111", remote, iface,
                          "Established", *self._cum[pid]))
        self._ifc["lo0"][0] += 300
        self._ifc["lo0"][1] += 300
        ifaces = {
            name: IfaceCounters(
                name=name, bytes_in=c[0], bytes_out=c[1], pkts_in=c[0] // 900,
                pkts_out=c[1] // 900, is_up=True, speed=1000 if name == "en0" else 0,
                mtu=16384 if name == "lo0" else 1500,
                ipv4={"lo0": ["127.0.0.1"], "en0": ["192.168.1.20"], "utun3": ["10.8.0.2"]}[name],
                mac="" if name != "en0" else "aa:bb:cc:00:11:22",
            )
            for name, c in self._ifc.items()
        }
        return Snapshot(ts=t, flows=flows, ifaces=ifaces, has_bytes=True,
                        notes=["DEMO MODE - data is synthetic"])
