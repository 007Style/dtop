"""The Textual application."""
from __future__ import annotations

import time

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import DataTable, Static

from dtop.collectors import Collector, CollectorError
from dtop.engine import Engine, Row
from dtop.fmt import human_dur
from dtop.screens import HelpScreen, KillScreen, NetScreen, DetailScreen
from dtop.widgets import (
    COLUMNS, REFRESH_CHOICES, render_footer, render_header, render_summary,
    render_throughput, row_cells,
)

STARTUP_SAMPLE_SECS = 2.0
SORTS = ("recent", "risk", "total", "name")


class DtopApp(App[None]):
    TITLE = "dtop"
    CSS = """
    Screen { background: #0b0f14; }
    #header { height: 1; background: #111820; }
    #top { height: 9; }
    #thru { width: 1fr; border: round #2a6f97; padding: 0 1; }
    #summary { width: 52; border: round #7c3aed; padding: 0 1; }
    #main { height: 1fr; }
    #table { height: 1fr; }
    #startup { height: 1fr; content-align: center middle; text-align: center; }
    #status { height: 1; color: #8b949e; }
    #footer { height: 1; background: #111820; }
    DataTable { background: #0b0f14; }
    DataTable > .datatable--header { background: #16202c; color: #58a6ff; text-style: bold; }
    DataTable > .datatable--cursor { background: #1f3a5f; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("h", "help", "Help"),
        Binding("question_mark", "help", "Help", show=False),
        Binding("p", "pause", "Pause"),
        Binding("n", "net", "Net"),
        Binding("k", "kill", "Kill"),
        Binding("s", "details", "Details"),
        Binding("a", "toggle_all", "All"),
        Binding("o", "cycle_sort", "Sort"),
        Binding("minus", "refresh_down", "Interval -", show=False),
        Binding("plus", "refresh_up", "Interval +", show=False),
        Binding("equals_sign", "refresh_up", "Interval +", show=False),
    ]

    def __init__(self, collector: Collector, refresh: float = 10.0) -> None:
        super().__init__()
        self.collector = collector
        self.refresh_secs = float(refresh)
        if int(refresh) not in REFRESH_CHOICES:
            self.refresh_secs = float(min(REFRESH_CHOICES, key=lambda c: abs(c - refresh)))
        self.engine = Engine(refresh=self.refresh_secs, synthetic=collector.synthetic)
        self.paused = False
        self.show_all = False
        self.sort = "recent"
        self.error = ""
        self._busy = False
        self._timer: Timer | None = None
        self._rows: list[Row] = []

    # ── layout ────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Horizontal(id="top"):
            yield Static(id="thru")
            yield Static(id="summary")
        with Vertical(id="main"):
            yield Static(id="startup")
            table = DataTable(id="table", cursor_type="row", zebra_stripes=True)
            table.display = False
            yield table
        yield Static(id="status")
        yield Static(render_footer(), id="footer")

    def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        for label, width in COLUMNS:
            table.add_column(label, width=width)
        table.focus()
        self._update_view()
        self._schedule()
        self._sample()

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    # ── sampling ──────────────────────────────────────────────────────────
    def _interval(self) -> float:
        return self.refresh_secs if self.engine.ready else min(self.refresh_secs, STARTUP_SAMPLE_SECS)

    def _schedule(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._timer = self.set_timer(self._interval(), self._tick)

    def _tick(self) -> None:
        if not self.paused:
            self._sample()
        self._schedule()

    @work(thread=True, exclusive=True, group="sample")
    def _sample(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            snap = self.collector.sample()
            err = ""
        except CollectorError as exc:
            snap, err = None, str(exc)
        except Exception as exc:  # keep the UI alive no matter what a collector does
            snap, err = None, f"{type(exc).__name__}: {exc}"
        finally:
            self._busy = False
        self.call_from_thread(self._apply, snap, err)

    def _apply(self, snap, err: str) -> None:
        self.error = err
        if snap is not None:
            self.engine.ingest(snap)
            if self._timer is not None and self.engine.ready and self.engine.samples <= 40:
                self._schedule()  # leave the fast startup cadence once warmed up
        self._update_view()
        scr = self.screen
        if hasattr(scr, "on_data"):
            scr.on_data()

    # ── view ──────────────────────────────────────────────────────────────
    def _selected_key(self) -> tuple[int, str] | None:
        table = self.query_one("#table", DataTable)
        if not self._rows or table.cursor_row is None:
            return None
        i = table.cursor_row
        return self._rows[i].key if 0 <= i < len(self._rows) else None

    def _update_view(self) -> None:
        e = self.engine
        shown_n, tracked = e.counts()
        self._rows = e.rows(show_all=self.show_all, sort=self.sort) if e.ready else []
        n_shown = len(self._rows)
        self.query_one("#header", Static).update(render_header(
            e, self.collector.name, self.paused, self._effective_refresh(), n_shown,
            tracked, self.show_all, self.sort, self.error))
        thru = self.query_one("#thru", Static)
        self.query_one("#thru", Static).update(render_throughput(e, max(10, thru.size.width - 4)))
        summ = self.query_one("#summary", Static)
        self.query_one("#summary", Static).update(render_summary(e, self._rows, tracked, summ.size.width))
        status = " · ".join(e.notes) if e.notes else ""
        self.query_one("#status", Static).update(Text(status, style="yellow", no_wrap=True, overflow="ellipsis"))

        table = self.query_one("#table", DataTable)
        startup = self.query_one("#startup", Static)
        if not e.ready:
            table.display = False
            startup.display = True
            remaining = max(0.0, 10.0 - e.telemetry_age)
            msg = Text(justify="center")
            msg.append("◈ dtop is gathering telemetry\n\n", style="bold bright_cyan")
            msg.append("Data will be displayed after ten seconds of telemetry has been collected.\n", style="white")
            msg.append(f"\n{remaining:0.0f}s remaining   ·   samples: {e.samples}\n\n", style="bright_yellow")
            msg.append("Desultory — occurring randomly, intermittently, without a steady pattern.\n", style="italic magenta")
            msg.append("Press h for help.", style="grey62")
            startup.update(msg)
            return
        startup.display = False
        table.display = True
        keep = self._selected_key()
        scroll_y = table.scroll_y
        table.clear()
        window = e.window
        for r in self._rows:
            table.add_row(*row_cells(r, window, e.has_bytes), key=f"{r.pid}|{r.name}")
        if keep is not None:
            for i, r in enumerate(self._rows):
                if r.key == keep:
                    table.move_cursor(row=i, animate=False)
                    break
        table.scroll_to(y=scroll_y, animate=False)
        if not self._rows:
            startup.display = True
            table.display = False
            msg = Text(justify="center")
            msg.append("No infrequent network talkers inside the current window yet.\n", style="bright_green")
            msg.append(f"Window {human_dur(e.window)} · keep dtop running, slow rhythms need time to show up.\n", style="grey62")
            msg.append("Press a to see every process.", style="grey62")
            startup.update(msg)

    def _effective_refresh(self) -> float:
        return self.refresh_secs

    # ── actions ───────────────────────────────────────────────────────────
    def action_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.screen.dismiss()
        elif len(self.screen_stack) == 1:
            self.push_screen(HelpScreen())

    def action_pause(self) -> None:
        self.paused = not self.paused
        if not self.paused:
            self._sample()  # catch up immediately on resume
        self._update_view()
        self.notify("paused - sampling stopped" if self.paused else "resumed", timeout=2)

    def action_net(self) -> None:
        if isinstance(self.screen, NetScreen):
            self.screen.dismiss()
        elif len(self.screen_stack) == 1:
            self.push_screen(NetScreen(self.engine))

    def action_kill(self) -> None:
        if isinstance(self.screen, KillScreen):
            self.screen.dismiss()
        elif len(self.screen_stack) == 1:
            rows = self.engine.rows(show_all=self.show_all, sort=self.sort)
            self.push_screen(KillScreen(rows, self._selected_key()))

    def action_details(self) -> None:
        if isinstance(self.screen, DetailScreen):
            self.screen.dismiss()
            return
        if len(self.screen_stack) != 1:
            return
        key = self._selected_key()
        if key is None:
            self.notify("select a process first (↑/↓)", timeout=2)
            return
        self.push_screen(DetailScreen(self.engine, key))

    def action_toggle_all(self) -> None:
        if len(self.screen_stack) != 1:
            return
        self.show_all = not self.show_all
        self._update_view()

    def action_cycle_sort(self) -> None:
        if len(self.screen_stack) != 1:
            return
        self.sort = SORTS[(SORTS.index(self.sort) + 1) % len(SORTS)]
        self._update_view()

    def _step_refresh(self, delta: int) -> None:
        idx = REFRESH_CHOICES.index(int(self.refresh_secs))
        idx = max(0, min(len(REFRESH_CHOICES) - 1, idx + delta))
        self.refresh_secs = float(REFRESH_CHOICES[idx])
        self.engine.set_refresh(self.refresh_secs)
        self._schedule()
        self._update_view()
        self.notify(f"refresh {int(self.refresh_secs)}s", timeout=2)

    def action_refresh_down(self) -> None:
        self._step_refresh(-1)

    def action_refresh_up(self) -> None:
        self._step_refresh(+1)
