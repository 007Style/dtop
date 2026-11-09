"""Pure rendering helpers (Rich Text) - no Textual state, easy to test."""
from __future__ import annotations

from rich.text import Text

from dtop import TAGLINE, __version__
from dtop.engine import Engine, Row
from dtop.fmt import ago, human_bytes, human_dur, human_rate

BLOCKS = " ▁▂▃▄▅▆▇█"

LEVEL_STYLE = {
    "HIGH": "bold white on red",
    "MED": "bold black on dark_orange",
    "LOW": "bold black on yellow",
    "OK": "green",
}
STATE_STYLE = {"ACTIVE": "bold bright_green", "quiet": "grey62", "EXITED": "bold red"}
REFRESH_CHOICES = (2, 5, 10, 30, 60, 120, 300)

KEYS = [
    ("h", "Help"), ("-/+", "Rate"), ("p", "Pause"), ("n", "Net"), ("k", "Kill"),
    ("s", "Details"), ("a", "All/Infrequent"), ("o", "Sort"), ("q", "Quit"),
]


def level_cell(level: str, score: int) -> Text:
    return Text(f" {level:<4}{score:>3} ", style=LEVEL_STYLE.get(level, ""))


def recency_style(seconds: float | None, window: float) -> str:
    if seconds is None:
        return "grey50"
    f = seconds / max(window, 1.0)
    if f < 0.1:
        return "bright_green"
    if f < 0.5:
        return "green3"
    if f < 1.0:
        return "yellow"
    return "dark_orange"


def spark_text(spark: str, hot: bool) -> Text:
    t = Text()
    for ch in spark:
        if ch == "·":
            t.append(ch, style="grey35")
        else:
            lvl = BLOCKS.index(ch) if ch in BLOCKS else 1
            t.append(ch, style=("bright_cyan" if lvl >= 6 else "cyan" if lvl >= 3 else "dodger_blue2"))
    if hot:
        t.stylize("bold")
    return t


def bar_chart(values: list[float], width: int, height: int, style: str) -> list[Text]:
    """Multi-row block chart (newest value at the right edge)."""
    vals = list(values)[-width:]
    vals = [0.0] * (width - len(vals)) + vals
    peak = max(vals) or 1.0
    total = height * 8
    scaled = [int(round(v / peak * total)) if v > 0 else 0 for v in vals]
    lines = []
    for r in range(height):
        base = (height - 1 - r) * 8
        t = Text()
        for sv in scaled:
            lvl = max(0, min(8, sv - base))
            t.append(BLOCKS[lvl], style=style)
        lines.append(t)
    return lines


def render_header(
    engine: Engine, collector_name: str, paused: bool, refresh: float,
    shown: int, tracked: int, show_all: bool, sort: str, error: str = "",
) -> Text:
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append(" ◈ dtop ", style="bold black on bright_cyan")
    t.append(f" v{__version__} ", style="bold bright_cyan")
    t.append("Desultory Top  ", style="italic grey70")
    t.append("│ ", style="grey35")
    if paused:
        t.append("⏸ PAUSED ", style="bold black on yellow")
    else:
        t.append("● LIVE ", style="bold bright_green")
    t.append("│ ", style="grey35")
    t.append("refresh ", style="grey62")
    t.append(f"{int(refresh)}s ", style="bold white")
    t.append("│ ", style="grey35")
    t.append("window ", style="grey62")
    t.append(f"{human_dur(engine.window)} ", style="bold magenta")
    t.append("(sliding) ", style="grey50")
    t.append("│ ", style="grey35")
    t.append("view ", style="grey62")
    t.append("ALL " if show_all else "INFREQUENT ", style="bold bright_yellow" if show_all else "bold bright_green")
    t.append(f"{shown}/{tracked} ", style="white")
    t.append("│ ", style="grey35")
    t.append("sort ", style="grey62")
    t.append(f"{sort} ", style="white")
    t.append("│ ", style="grey35")
    t.append(collector_name, style="grey50")
    if error:
        t.append(f"  ⚠ {error}", style="bold red")
    return t


def render_throughput(engine: Engine, width: int) -> Text:
    width = max(10, width)
    hist = engine.history
    ins = [h[1] for h in hist]
    outs = [h[2] for h in hist]
    cur_in = ins[-1] if ins else 0.0
    cur_out = outs[-1] if outs else 0.0
    out = Text()
    out.append("NETWORK THROUGHPUT ", style="bold bright_cyan")
    out.append(f"(all non-loopback interfaces, last {len(hist)} samples)\n", style="grey50")
    out.append(f"▼ IN  {human_rate(cur_in):>11}  peak {human_rate(max(ins or [0])):>11}\n", style="bold bright_green")
    for ln in bar_chart(ins, width, 2, "green3"):
        out.append_text(ln)
        out.append("\n")
    out.append(f"▲ OUT {human_rate(cur_out):>11}  peak {human_rate(max(outs or [0])):>11}\n", style="bold bright_red")
    lines = bar_chart(outs, width, 2, "red3")
    for i, ln in enumerate(lines):
        out.append_text(ln)
        if i < len(lines) - 1:
            out.append("\n")
    return out


def render_summary(engine: Engine, rows: list[Row], tracked: int, width: int) -> Text:
    t = Text()
    t.append("DESULTORY WINDOW\n", style="bold magenta")
    t.append(f"  sliding window  {human_dur(engine.window)}\n", style="white")
    if engine.p90 is not None:
        t.append(f"  from slowest rhythm P90 {human_dur(engine.p90)} × 4\n", style="grey50")
    else:
        t.append("  floor only - no rhythms learned yet\n", style="grey50")
    t.append(f"  telemetry age   {human_dur(engine.telemetry_age)}\n", style="white")
    counts = {k: 0 for k in ("HIGH", "MED", "LOW", "OK")}
    for r in rows:
        counts[r.level] += 1
    t.append(f"  tracked {tracked}  shown {len(rows)}\n", style="white")
    t.append("  risk ", style="grey62")
    for k in ("HIGH", "MED", "LOW", "OK"):
        t.append(f" {k} {counts[k]} ", style=LEVEL_STYLE[k])
        t.append(" ")
    t.append("\n")
    act = [h[3] for h in engine.history]
    from dtop.fmt import sparkline
    t.append("  active procs ", style="grey62")
    t.append(sparkline([float(x) for x in act], max(8, min(24, width - 20))), style="bright_cyan")
    if not engine.has_bytes:
        t.append("\n  ⚠ byte counters unavailable on this OS", style="yellow")
    return t


def render_footer() -> Text:
    t = Text(no_wrap=True, overflow="ellipsis")
    for k, label in KEYS:
        t.append(f" {k} ", style="bold black on bright_cyan")
        t.append(f" {label} ", style="grey70")
    t.append(f"│ {TAGLINE}", style="italic grey50")
    return t


def row_cells(r: Row, window: float, now_known: bool) -> list[Text | str]:
    na = "n/a"
    ifaces = ",".join(r.ifaces[:3]) + ("+" if len(r.ifaces) > 3 else "") or "-"
    remote = r.remotes[0] if r.remotes else "-"
    if len(r.remotes) > 1:
        remote += f" +{len(r.remotes) - 1}"
    byt = r.bytes_known
    return [
        level_cell(r.level, r.score),
        Text(str(r.pid), style="grey70"),
        Text(r.name, style="bold white"),
        Text(r.state, style=STATE_STYLE.get(r.state, "")),
        Text(ago(r.since_last), style=recency_style(r.since_last, window)),
        Text(human_bytes(r.last_in) if byt else na, style="green3"),
        Text(human_bytes(r.last_out) if byt else na, style="red3"),
        Text(human_bytes(r.total_in) if byt else na, style="bright_green"),
        Text(human_bytes(r.total_out) if byt else na, style="bright_red"),
        Text(ifaces, style="bright_yellow"),
        Text(r.pattern, style="magenta"),
        spark_text(r.spark, r.state == "ACTIVE"),
        Text(remote, style="grey70"),
    ]


COLUMNS = [
    ("RISK", 9), ("PID", 7), ("PROCESS", 24), ("STATE", 7), ("LAST COMM", 11),
    ("▼ LAST", 9), ("▲ LAST", 9), ("▼ TOTAL", 9), ("▲ TOTAL", 9),
    ("IFACE", 12), ("PATTERN", 16), ("ACTIVITY (window)", 20), ("REMOTE", 24),
]


def render_ifaces(engine: Engine) -> Text:
    rows = engine.iface_rows()
    t = Text()
    tin = sum(r.total_in for r in rows if not r.name.startswith("lo"))
    tout = sum(r.total_out for r in rows if not r.name.startswith("lo"))
    t.append("TOTAL SINCE dtop STARTED (non-loopback)   ", style="bold bright_cyan")
    t.append(f"▼ {human_bytes(tin)}   ", style="bold bright_green")
    t.append(f"▲ {human_bytes(tout)}\n\n", style="bold bright_red")
    for r in rows:
        led = ("● UP  ", "bold bright_green") if r.is_up else ("○ DOWN", "bold red")
        t.append(f" {led[0]} ", style=led[1])
        t.append(f"{r.name:<10}", style="bold white")
        sp = f"{r.speed} Mb/s" if r.speed else "speed ?"
        t.append(f" {sp:>10}  mtu {r.mtu or '?':<5}", style="grey62")
        if r.mac:
            t.append(f" {r.mac}", style="grey50")
        t.append("\n")
        t.append("        ")
        t.append(f"▼ {human_rate(r.rate_in):>11}", style="green3")
        t.append(f"  ▲ {human_rate(r.rate_out):>11}", style="red3")
        t.append(f"   total ▼ {human_bytes(r.total_in):>9} ▲ {human_bytes(r.total_out):>9}", style="white")
        t.append("  ")
        t.append(r.spark, style="cyan")
        t.append("\n")
        addrs = ", ".join(r.ipv4 + r.ipv6[:1]) or "no address"
        t.append(f"        {addrs}", style="bright_yellow")
        extra = f"   flows {r.nflows}"
        if r.errs or r.drops:
            extra += f"   errs {r.errs} drops {r.drops}"
        t.append(extra + "\n", style="grey62")
        if r.procs:
            t.append("        used by: ", style="grey50")
            t.append(", ".join(r.procs[:8]) + (" …" if len(r.procs) > 8 else ""), style="magenta")
            t.append("\n")
        t.append("\n")
    return t
