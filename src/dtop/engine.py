"""Telemetry engine: deltas -> episodes -> adaptive sliding window -> rows.

How "desultory" is decided (all explainable, nothing hidden):

* Every sample, each flow's byte counters are diffed against the previous
  sample. A positive delta (or a brand-new flow) is *activity* for its process.
* Consecutive active samples are merged into one *episode*. "Last
  communication" is the most recent episode (bytes in / out for that episode).
* The *window* slides and adapts. It is 4x the P90 of every process's typical
  gap between episodes, so if something on this machine talks only every two
  hours, the window grows to cover it. It never drops below a floor of
  max(30s, 3 x refresh) and never exceeds 24h (or the time dtop has run).
* A process is *desultory* when it was active in at most DUTY_MAX of the samples
  inside the window (so continuous streamers are filtered out) and its last
  communication still falls within max(window, 2.5 x its own rhythm). Processes
  seen exactly once stay listed for SINGLE_GRACE seconds.
"""
from __future__ import annotations

import bisect
import statistics
from dataclasses import dataclass, field
from typing import Callable

import psutil

from dtop import risk
from dtop.fmt import human_dur, sparkline
from dtop.models import Flow, IfaceCounters, Snapshot
from dtop.procinfo import InfoCache, ProcInfo

STARTUP_SECS = 10.0
FLOOR_SECS = 30.0
CEILING_SECS = 86400.0
SINGLE_GRACE = 900.0
DUTY_MAX = 0.25
GAP_HISTORY = 8
WINDOW_FACTOR = 4.0
SPARK_CELLS = 20
MAX_LIST = 4096

SPARK_CHARS = " ▁▂▃▄▅▆▇█"


def _trim(lst: list, limit: int = MAX_LIST) -> None:
    if len(lst) > limit * 2:
        del lst[: len(lst) - limit]


@dataclass
class ProcTrack:
    pid: int
    name: str
    first_seen: float
    present: bool = True
    total_in: int = 0
    total_out: int = 0
    last_active: float | None = None
    ep_in: int = 0
    ep_out: int = 0
    active_prev: bool = False
    episodes: list[float] = field(default_factory=list)
    events: list[tuple[float, int]] = field(default_factory=list)
    iface_bytes: dict[str, list[int]] = field(default_factory=dict)
    iface_last: dict[str, float] = field(default_factory=dict)
    remotes: dict[str, float] = field(default_factory=dict)
    nflows: int = 0
    bytes_known: bool = False

    def gaps(self) -> list[float]:
        eps = self.episodes[-(GAP_HISTORY + 1):]
        return [b - a for a, b in zip(eps, eps[1:])]

    def typical_gap(self) -> float | None:
        g = self.gaps()
        return statistics.median(g) if g else None


@dataclass
class Row:
    key: tuple[int, str]
    pid: int
    name: str
    state: str  # ACTIVE | quiet | EXITED
    last_active: float | None
    since_last: float | None
    last_in: int
    last_out: int
    total_in: int
    total_out: int
    episodes: int
    gap: float | None
    pattern: str
    ifaces: list[str]
    remotes: list[str]
    nflows: int
    score: int
    level: str
    reasons: list[str]
    first_seen: float
    spark: str
    bytes_known: bool
    iface_detail: list[tuple[str, int, int, float]]  # iface, in, out, last_ts
    remote_detail: list[tuple[str, float]]  # remote, last_ts


@dataclass
class IfaceRow:
    name: str
    is_up: bool
    speed: int
    mtu: int
    ipv4: list[str]
    ipv6: list[str]
    mac: str
    total_in: int
    total_out: int
    rate_in: float
    rate_out: float
    errs: int
    drops: int
    nflows: int
    procs: list[str]
    spark: str = ""


def _is_loop(name: str) -> bool:
    return name.startswith("lo")


class Engine:
    def __init__(
        self,
        refresh: float = 10.0,
        info_fn: Callable[[int, str], ProcInfo | None] | None = None,
        pid_alive: Callable[[int], bool] | None = None,
        synthetic: bool = False,
    ) -> None:
        self.synthetic = synthetic
        if synthetic:
            info_fn = lambda pid, name: None  # noqa: E731 - fake pids must not hit real processes
            pid_alive = lambda pid: True  # noqa: E731
        self.refresh = refresh
        self.cache = InfoCache()
        self.info_fn = info_fn or self.cache.light
        self.pid_alive = pid_alive or psutil.pid_exists
        self.procs: dict[tuple[int, str], ProcTrack] = {}
        self._prev: dict[tuple, tuple[int, int]] = {}
        self.sample_ts: list[float] = []
        self.started: float | None = None
        self.now: float = 0.0
        self.samples = 0
        self.has_bytes = True
        self.notes: list[str] = []
        self._if_base: dict[str, IfaceCounters] = {}
        self._if_prev: dict[str, IfaceCounters] = {}
        self._if_rate: dict[str, tuple[float, float]] = {}
        self._if_hist: dict[str, list[float]] = {}
        self.ifaces: dict[str, IfaceCounters] = {}
        self._flows_now: list[Flow] = []
        # system history for graphs: (ts, rate_in, rate_out, n_active)
        self.history: list[tuple[float, float, float, int]] = []
        self._window_cache: float = FLOOR_SECS
        self.p90: float | None = None  # P90 of per-process rhythms driving the window

    # ── ingest ────────────────────────────────────────────────────────────
    @property
    def telemetry_age(self) -> float:
        return 0.0 if self.started is None else self.now - self.started

    @property
    def ready(self) -> bool:
        return self.samples >= 2 and self.telemetry_age >= STARTUP_SECS

    def set_refresh(self, refresh: float) -> None:
        self.refresh = refresh

    def ingest(self, snap: Snapshot) -> None:
        ts = snap.ts
        first = self.started is None
        dt = 0.0 if first else max(ts - self.now, 1e-6)
        if first:
            self.started = ts
        self.now = ts
        self.samples += 1
        self.has_bytes = snap.has_bytes
        self.notes = snap.notes
        self.sample_ts.append(ts)
        _trim(self.sample_ts, 50_000)

        merged: dict[tuple, Flow] = {}
        for f in snap.flows:
            old = merged.get(f.key)
            if old is None:
                merged[f.key] = f
            elif f.bytes_in is not None and old.bytes_in is not None:
                merged[f.key] = Flow(
                    f.pid, f.name, f.proto, f.local, f.remote, f.iface, f.state,
                    old.bytes_in + f.bytes_in, (old.bytes_out or 0) + (f.bytes_out or 0),
                )
        self._flows_now = list(merged.values())

        present: set[tuple[int, str]] = set()
        active: dict[tuple[int, str], list[int]] = {}
        nflows: dict[tuple[int, str], int] = {}
        new_prev: dict[tuple, tuple[int, int]] = {}

        for f in merged.values():
            pk = (f.pid, f.name)
            present.add(pk)
            tr = self.procs.get(pk)
            if tr is None:
                tr = self.procs[pk] = ProcTrack(f.pid, f.name, ts)
            nflows[pk] = nflows.get(pk, 0) + 1
            if f.remote and f.remote not in ("*:*", "*.*", "*"):
                tr.remotes[f.remote] = ts
                if len(tr.remotes) > 50:
                    tr.remotes.pop(min(tr.remotes, key=tr.remotes.get))

            known = f.bytes_in is not None
            if known:
                tr.bytes_known = True
            new_prev[f.key] = (f.bytes_in or 0, f.bytes_out or 0)
            if first:
                continue
            prev = self._prev.get(f.key)
            if known:
                if prev is None:
                    din, dout, act = f.bytes_in, f.bytes_out or 0, True
                else:
                    din, dout = f.bytes_in - prev[0], (f.bytes_out or 0) - prev[1]
                    if din < 0 or dout < 0:  # counter reset / key reuse
                        din, dout = f.bytes_in, f.bytes_out or 0
                    act = din > 0 or dout > 0
            else:
                din = dout = 0
                act = prev is None  # bytes unknown: a new flow is the activity
            if act:
                a = active.setdefault(pk, [0, 0])
                a[0] += din
                a[1] += dout
                if f.iface:
                    ib = tr.iface_bytes.setdefault(f.iface, [0, 0])
                    ib[0] += din
                    ib[1] += dout
                    tr.iface_last[f.iface] = ts
            elif f.iface:
                tr.iface_last.setdefault(f.iface, ts)
                tr.iface_bytes.setdefault(f.iface, [0, 0])

        self._prev = new_prev

        for pk, tr in self.procs.items():
            tr.present = pk in present
            tr.nflows = nflows.get(pk, 0)
            a = active.get(pk)
            if a is None:
                tr.active_prev = False
                continue
            din, dout = a
            if not tr.active_prev:
                tr.episodes.append(ts)
                _trim(tr.episodes, 256)
                tr.ep_in = tr.ep_out = 0
            tr.ep_in += din
            tr.ep_out += dout
            tr.total_in += din
            tr.total_out += dout
            tr.last_active = ts
            tr.active_prev = True
            tr.events.append((ts, din + dout))
            _trim(tr.events)

        self._ingest_ifaces(snap.ifaces, dt)
        r_in = sum(v[0] for k, v in self._if_rate.items() if not _is_loop(k))
        r_out = sum(v[1] for k, v in self._if_rate.items() if not _is_loop(k))
        if not first:
            self.history.append((ts, r_in, r_out, len(active)))
            _trim(self.history, 1200)

        self._prune()
        self._window_cache = self._compute_window()

    def _ingest_ifaces(self, cur: dict[str, IfaceCounters], dt: float) -> None:
        for name, c in cur.items():
            if name not in self._if_base:
                self._if_base[name] = c
            p = self._if_prev.get(name)
            if p is not None and dt > 0:
                self._if_rate[name] = (
                    max(0, c.bytes_in - p.bytes_in) / dt,
                    max(0, c.bytes_out - p.bytes_out) / dt,
                )
            else:
                self._if_rate.setdefault(name, (0.0, 0.0))
            h = self._if_hist.setdefault(name, [])
            h.append(sum(self._if_rate[name]))
            _trim(h, 120)
        self._if_prev = dict(cur)
        self.ifaces = dict(cur)

    def _prune(self) -> None:
        keep = max(self._window_cache, 3600.0)
        dead = [
            k for k, t in self.procs.items()
            if not t.present and (t.last_active or t.first_seen) < self.now - keep
            and not self.pid_alive(t.pid)
        ]
        for k in dead:
            del self.procs[k]

    # ── window ───────────────────────────────────────────────────────────
    def _compute_window(self) -> float:
        floor = max(FLOOR_SECS, 3 * self.refresh)
        gaps = sorted(g for t in self.procs.values() if (g := t.typical_gap()) is not None)
        if not gaps:
            self.p90 = None
            return floor
        p90 = gaps[max(0, -(-len(gaps) * 9 // 10) - 1)]
        self.p90 = p90
        return max(floor, min(WINDOW_FACTOR * p90, CEILING_SECS))

    @property
    def window(self) -> float:
        """Effective sliding window in seconds (adaptive, bounded by uptime)."""
        floor = max(FLOOR_SECS, 3 * self.refresh)
        return max(floor, min(self._window_cache, self.telemetry_age or floor))

    # ── classification ───────────────────────────────────────────────────
    def _duty(self, tr: ProcTrack, w: float) -> float:
        cutoff = self.now - w
        n = len(self.sample_ts) - bisect.bisect_left(self.sample_ts, cutoff)
        if n < 4:
            return 0.0
        idx = bisect.bisect_left([e[0] for e in tr.events], cutoff)
        return (len(tr.events) - idx) / n

    def _is_desultory(self, tr: ProcTrack, w: float) -> bool:
        if tr.last_active is None:
            return False
        age = self.now - tr.last_active
        gap = tr.typical_gap()
        if gap is None:
            if age > max(w, SINGLE_GRACE):
                return False
        elif age > max(w, 2.5 * gap):
            return False
        return self._duty(tr, w) <= DUTY_MAX

    def _pattern(self, tr: ProcTrack) -> str:
        n = len(tr.episodes)
        if n == 0:
            return "silent"
        if n == 1:
            return "single"
        g = tr.gaps()
        if len(g) < 3:
            return f"seen {n}x"
        mean = statistics.fmean(g)
        sd = statistics.pstdev(g)
        if sd <= max(0.25 * mean, self.refresh):
            return f"periodic ~{human_dur(mean)}"
        return "irregular"

    def _spark(self, tr: ProcTrack, w: float) -> str:
        cells = [0] * SPARK_CELLS
        start = self.now - w
        for ts, b in reversed(tr.events):
            if ts < start:
                break
            i = min(SPARK_CELLS - 1, int((ts - start) / w * SPARK_CELLS))
            cells[i] += b
        peak = max(cells)
        if peak == 0:
            return "·" * SPARK_CELLS
        out = []
        for v in cells:
            if v == 0:
                out.append("·")
            else:
                out.append(SPARK_CHARS[max(1, min(8, int(v / peak * 8 + 0.999)))])
        return "".join(out)

    def _row(self, tr: ProcTrack, w: float) -> Row:
        info = self.info_fn(tr.pid, tr.name)
        alive = tr.present or self.pid_alive(tr.pid)
        if not alive:
            state = "EXITED"
        elif tr.last_active == self.now and self.samples > 1:
            state = "ACTIVE"
        else:
            state = "quiet"
        pattern = self._pattern(tr)
        recent_remote = max(tr.remotes, key=tr.remotes.get) if tr.remotes else ""
        score, why = risk.assess(
            name=tr.name, pattern=pattern, remote=recent_remote,
            total_in=tr.total_in, total_out=tr.total_out,
            bytes_known=tr.bytes_known, info=info,
        )
        name = info.name if info and info.name else tr.name  # nettop truncates to 15 chars
        return Row(
            key=(tr.pid, tr.name), pid=tr.pid, name=name, state=state,
            last_active=tr.last_active,
            since_last=None if tr.last_active is None else self.now - tr.last_active,
            last_in=tr.ep_in, last_out=tr.ep_out,
            total_in=tr.total_in, total_out=tr.total_out,
            episodes=len(tr.episodes), gap=tr.typical_gap(), pattern=pattern,
            ifaces=sorted(tr.iface_last, key=tr.iface_last.get, reverse=True),
            remotes=sorted(tr.remotes, key=tr.remotes.get, reverse=True),
            nflows=tr.nflows, score=score, level=risk.level(score), reasons=why,
            first_seen=tr.first_seen, spark=self._spark(tr, w),
            bytes_known=tr.bytes_known,
            iface_detail=[
                (i, b[0], b[1], tr.iface_last.get(i, 0.0))
                for i, b in sorted(tr.iface_bytes.items(), key=lambda kv: -tr.iface_last.get(kv[0], 0.0))
            ],
            remote_detail=sorted(tr.remotes.items(), key=lambda kv: -kv[1]),
        )

    def row_for(self, key: tuple[int, str]) -> Row | None:
        tr = self.procs.get(key)
        return None if tr is None else self._row(tr, self.window)

    def rows(self, show_all: bool = False, sort: str = "recent") -> list[Row]:
        w = self.window
        out = [
            self._row(tr, w) for tr in self.procs.values()
            if show_all or self._is_desultory(tr, w)
        ]
        if sort == "risk":
            out.sort(key=lambda r: (-r.score, r.since_last if r.since_last is not None else 9e12))
        elif sort == "total":
            out.sort(key=lambda r: -(r.total_in + r.total_out))
        elif sort == "name":
            out.sort(key=lambda r: (r.name.lower(), r.pid))
        else:
            out.sort(key=lambda r: (r.since_last if r.since_last is not None else 9e12, r.name))
        return out

    def counts(self) -> tuple[int, int]:
        """(desultory, tracked) process counts."""
        w = self.window
        d = sum(1 for t in self.procs.values() if self._is_desultory(t, w))
        return d, len(self.procs)

    # ── interfaces ───────────────────────────────────────────────────────
    def iface_rows(self) -> list[IfaceRow]:
        per_if_flows: dict[str, int] = {}
        per_if_procs: dict[str, set[str]] = {}
        for f in self._flows_now:
            if f.iface:
                per_if_flows[f.iface] = per_if_flows.get(f.iface, 0) + 1
                per_if_procs.setdefault(f.iface, set()).add(f.name)
        rows = []
        for name, c in self.ifaces.items():
            base = self._if_base.get(name, c)
            rin, rout = self._if_rate.get(name, (0.0, 0.0))
            rows.append(IfaceRow(
                name=name, is_up=c.is_up, speed=c.speed, mtu=c.mtu,
                ipv4=c.ipv4, ipv6=c.ipv6, mac=c.mac,
                total_in=max(0, c.bytes_in - base.bytes_in),
                total_out=max(0, c.bytes_out - base.bytes_out),
                rate_in=rin, rate_out=rout, errs=c.errs, drops=c.drops,
                nflows=per_if_flows.get(name, 0),
                procs=sorted(per_if_procs.get(name, ()), key=str.lower),
                spark=sparkline(self._if_hist.get(name, []), 20),
            ))
        rows.sort(key=lambda r: (not r.is_up, -(r.total_in + r.total_out), r.name))
        return rows
