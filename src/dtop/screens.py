"""Modal dialogs: help, network interfaces, kill picker, process details."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from dtop import TAGLINE, __version__, killer, procinfo
from dtop.engine import Engine, Row
from dtop.fmt import ago, human_bytes, human_dur
from dtop.widgets import LEVEL_STYLE, level_cell, render_ifaces

if TYPE_CHECKING:  # pragma: no cover
    pass


class DtopModal(ModalScreen[None]):
    """Base: centred bordered dialog that closes on Esc or its toggle key."""

    TOGGLE_KEY = ""
    DEFAULT_CSS = """
    DtopModal { align: center middle; background: $background 60%; }
    DtopModal > #dialog {
        width: 86%; max-width: 110; height: 86%;
        border: round cyan; background: $surface; padding: 0 1;
    }
    DtopModal #title { text-style: bold; color: cyan; height: 1; }
    DtopModal #body { height: 1fr; }
    DtopModal #hint { height: 1; color: $text-muted; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._bindings.bind("escape", "close", "Close", show=False)
        if self.TOGGLE_KEY:
            self._bindings.bind(self.TOGGLE_KEY, "close", "Close", show=False)

    def action_close(self) -> None:
        self.dismiss()

    def on_data(self) -> None:
        """Called by the app after each new sample (live screens override)."""


# ── help ────────────────────────────────────────────────────────────────────
HELP_TEXT = f"""\
[b cyan]dtop[/] — [b]D[/]esultory [b]Top[/]   v{__version__}

[b magenta]desultory[/] [i](adj.)[/]  Occurring randomly, intermittently, or without a steady pattern.

dtop watches every process on the machine and surfaces the ones that talk on
the network only [i]rarely[/] — a few packets every few seconds, minutes or even
hours. Those quiet, intermittent talkers are exactly where beacons, stale
background helpers and processes you [i]thought[/] you had quit like to hide.

[b cyan]HOW THE SLIDING WINDOW WORKS[/]
  • Each refresh dtop diffs every flow's byte counters; a change is "communication".
  • Back-to-back active samples merge into one episode — that is a "last comm".
  • dtop learns each process's rhythm (the typical gap between episodes). The
    window slides to 4× the P90 of those rhythms, so a machine whose helpers
    chirp every 2 hours automatically gets a multi-hour window. It never shrinks
    below max(30s, 3× refresh).
  • Processes that talk continuously (> 25% of samples) are filtered out; only
    desultory ones remain. Press [b]a[/] to see everything.

[b cyan]HOT KEYS[/]
  [b]h[/] or [b]?[/]    this help (press again or Esc to close)
  [b]-[/]  [b]+[/]      slower / faster refresh: 2s 5s [b]10s[/] 30s 60s 120s 300s
  [b]p[/]          pause / resume sampling
  [b]n[/]          network interfaces: state, address, speed, per-interface
               rates, running IN/OUT totals, and which processes use each
  [b]k[/]          KILL picker: ↑/↓ choose, Enter = hard kill (SIGKILL / kill -9).
               Press [b]k[/] again or Esc to dismiss without killing
  [b]s[/]          details for the highlighted process (what it is, uptime, path,
               parent, threads, sockets, per-interface bytes, remote peers, risk)
  [b]a[/]          toggle ALL processes  ↔  only the infrequent (desultory) ones
  [b]o[/]          cycle sort: last-comm → risk → total bytes → name
  [b]↑ ↓ PgUp PgDn Home End[/]  move through the table      [b]q[/]  quit

[b cyan]READING THE TABLE[/]
  RISK       heuristic triage score — HIGH/MED/LOW/OK (details in [b]s[/]). It is a
             hint to look closer, [i]not[/] a verdict.
  STATE      ACTIVE = talked this sample · quiet = idle · EXITED = process gone
  LAST COMM  how long ago it last sent/received;  ▼ LAST / ▲ LAST = bytes in /
             out during that last episode;  ▼ TOTAL / ▲ TOTAL = running totals
             since dtop started collecting telemetry
  IFACE      interface(s) its traffic used (most recent first)
  PATTERN    periodic ~Ns (clock-like, beacon-ish) · irregular · single · seen Nx
  ACTIVITY   sparkline across the current window, oldest → newest

[b cyan]TIPS[/]
  • Sort by risk ([b]o[/]) and look for periodic + public IP + upload-heavy rows.
  • EXITED rows that still show traffic are things you may have thought you quit.
  • Run with sudo / Administrator to see sockets owned by other users.
  • --demo shows synthetic data; --doctor checks your platform's capabilities.

[i]{TAGLINE}[/]
"""


class HelpScreen(DtopModal):
    TOGGLE_KEY = "h"

    def __init__(self) -> None:
        super().__init__()
        self._bindings.bind("question_mark", "close", "Close", show=False)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("◈ dtop help", id="title")
            with VerticalScroll(id="body"):
                yield Static(HELP_TEXT, markup=True)
            yield Static("h / Esc close · ↑↓ scroll", id="hint")


# ── network interfaces ─────────────────────────────────────────────────────────
class NetScreen(DtopModal):
    TOGGLE_KEY = "n"

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("◈ network interfaces — live rates & running totals", id="title")
            with VerticalScroll(id="body"):
                yield Static(render_ifaces(self.engine), id="ifaces")
            yield Static("n / Esc close · ↑↓ scroll", id="hint")

    def on_data(self) -> None:
        self.query_one("#ifaces", Static).update(render_ifaces(self.engine))


# ── kill picker ──────────────────────────────────────────────────────────────
class KillScreen(ModalScreen[None]):
    """Small picker. Enter = immediate hard kill. k or Esc dismisses."""

    DEFAULT_CSS = """
    KillScreen { align: center middle; background: $background 60%; }
    KillScreen > #kdialog {
        width: 78; max-width: 95%; height: 70%; max-height: 24;
        border: heavy red; background: $surface; padding: 0 1;
    }
    KillScreen #ktitle { text-style: bold; color: red; height: 1; }
    KillScreen #ksub { color: $text-muted; height: 1; }
    KillScreen OptionList { height: 1fr; border: none; padding: 0; background: $surface; }
    """

    def __init__(self, rows: list[Row], preselect: tuple[int, str] | None) -> None:
        super().__init__()
        self.rows = rows
        self.preselect = preselect
        self._bindings.bind("escape", "close", "Close", show=False)
        self._bindings.bind("k", "close", "Close", show=False)

    def compose(self) -> ComposeResult:
        with Vertical(id="kdialog"):
            yield Static("☠  KILL PROCESS  (kill -9)", id="ktitle")
            yield Static("↑/↓ select · Enter = KILL NOW · k or Esc = cancel", id="ksub")
            opts = []
            for r in self.rows:
                label = Text()
                label.append(f"{r.pid:>7} ", style="grey70")
                label.append(f"{r.name:<22.22}", style="bold white")
                label.append(f" {ago(r.since_last):>10} ", style="grey62")
                label.append_text(level_cell(r.level, r.score))
                opts.append(Option(label, id=f"{r.pid}"))
            if not opts:
                opts.append(Option("(no processes to show)", disabled=True))
            yield OptionList(*opts, id="klist")

    def on_mount(self) -> None:
        ol = self.query_one("#klist", OptionList)
        ol.focus()
        for i, r in enumerate(self.rows):
            if r.key == self.preselect:
                ol.highlighted = i
                return
        if self.rows:
            ol.highlighted = 0

    def action_close(self) -> None:
        self.dismiss()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        pid = int(event.option.id)
        if getattr(self.app.engine, "synthetic", False):
            self.app.notify("demo mode: PIDs are synthetic, nothing was killed", title="kill disabled", severity="warning")
            self.dismiss()
            return
        ok, msg = killer.kill_pid(pid)
        self.app.notify(msg, title="kill -9" if ok else "kill failed",
                        severity="warning" if ok else "error", timeout=6)
        self.dismiss()


# ── process details ──────────────────────────────────────────────────────────
class DetailScreen(DtopModal):
    TOGGLE_KEY = "s"

    def __init__(self, engine: Engine, key: tuple[int, str]) -> None:
        super().__init__()
        self.engine = engine
        self.key = key

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("◈ process details", id="title")
            with VerticalScroll(id="body"):
                yield Static(self._build(), id="detail")
            yield Static("s / Esc close · ↑↓ scroll", id="hint")

    def on_data(self) -> None:
        self.query_one("#detail", Static).update(self._build())

    def _build(self) -> Text:
        pid, name = self.key
        row = self.engine.row_for(self.key)
        info = None if self.engine.synthetic else procinfo.get_info(pid, full=True)
        t = Text()

        def h(s: str) -> None:
            t.append(f"\n{s}\n", style="bold cyan")

        def kv(k: str, v: str, style: str = "white") -> None:
            t.append(f"  {k:<14}", style="grey62")
            t.append(f"{v}\n", style=style)

        t.append(f"{info.name if info and info.name else name}", style="bold bright_white")
        t.append(f"  (pid {pid})\n", style="grey70")
        if self.engine.synthetic:
            t.append("\n  DEMO MODE: process metadata is not available for synthetic PIDs.\n", style="yellow")
        elif info is None:
            t.append("\n  This process has EXITED. dtop is still showing its history.\n", style="bold red")
        else:
            h("WHAT IT IS")
            kv("executable", info.exe or "(unavailable)")
            kv("command line", info.cmdline or "(unavailable)")
            kv("user", info.user or "?")
            kv("parent", f"{info.parent_name} (pid {info.ppid})" if info.ppid else "-")
            kv("status", info.status or "?")
            kv("alive for", human_dur(info.uptime))
            kv("threads", str(info.threads))
            for th in info.thread_list:
                kv("", th, "grey70")
            kv("cpu / memory", f"{info.cpu:.1f}%  /  {human_bytes(info.rss)}")
            kv("working dir", info.cwd or "-")
            kv("open files", str(info.open_files))
            if info.conns:
                h("SOCKETS RIGHT NOW")
                for c in info.conns[:12]:
                    kv("", c, "bright_yellow")
            for n in sorted(set(info.notes)):
                kv("note", n, "yellow")
        if row is not None:
            h("NETWORK BEHAVIOUR (as seen by dtop)")
            kv("last comm", ago(row.since_last), "bright_green")
            kv("last episode", f"▼ {human_bytes(row.last_in)}   ▲ {human_bytes(row.last_out)}")
            kv("totals", f"▼ {human_bytes(row.total_in)}   ▲ {human_bytes(row.total_out)}")
            kv("episodes", f"{row.episodes}   typical gap {human_dur(row.gap)}")
            kv("pattern", row.pattern, "magenta")
            kv("first seen", f"{human_dur(self.engine.now - row.first_seen)} ago")
            if not row.bytes_known:
                kv("note", "byte counters unavailable on this OS - activity inferred from new sockets", "yellow")
            if row.ifaces:
                h("INTERFACES USED")
                for ifn, bi, bo, last in row.iface_detail:
                    kv(ifn, f"▼ {human_bytes(bi):>9}  ▲ {human_bytes(bo):>9}   last {ago(self.engine.now - last)}", "bright_yellow")
            if row.remote_detail:
                h("REMOTE PEERS")
                for rem, last in row.remote_detail[:10]:
                    kv("", f"{rem:<34} last {ago(self.engine.now - last)}", "grey85")
            h("RISK")
            t.append("  ")
            t.append_text(level_cell(row.level, row.score))
            t.append("\n")
            if row.reasons:
                for r in row.reasons:
                    t.append(f"  • {r}\n", style=LEVEL_STYLE.get(row.level, "white") if row.level in ("HIGH",) else "white")
            else:
                t.append("  nothing unusual detected\n", style="green")
        t.append("\n  Press ", style="grey50")
        t.append("s", style="bold")
        t.append(" or Esc to close, then ", style="grey50")
        t.append("k", style="bold")
        t.append(" to kill.\n", style="grey50")
        return t
