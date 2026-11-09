"""Headless UI tests: drive every hot key through Textual's pilot."""
from __future__ import annotations

import pytest
from textual.widgets import DataTable, OptionList

import dtop.app as appmod
from dtop import killer
from dtop.app import DtopApp
from dtop.collectors.base import Collector
from dtop.models import Flow, IfaceCounters, Snapshot
from dtop.screens import DetailScreen, HelpScreen, KillScreen, NetScreen


class FakeCollector(Collector):
    """Advances a fake clock 6s per sample; 'beacon' talks every 5th sample."""

    name = "fake"

    def __init__(self):
        self.t = 5000.0
        self.n = 0
        self.b = 0

    def sample(self):
        self.t += 6
        self.n += 1
        if self.n % 5 == 0:
            self.b += 1000
        flows = [
            Flow(4242, "beacon", "tcp4", "10.0.0.2:1", "45.9.148.108:4444", "en0", "Established", self.b, self.b // 10),
            Flow(4343, "chatty", "tcp4", "10.0.0.2:2", "8.8.8.8:443", "en0", "Established", self.n * 99, self.n * 9),
        ]
        ifs = {"en0": IfaceCounters("en0", self.n * 1000, self.n * 100, ipv4=["10.0.0.2"], speed=1000, mtu=1500)}
        return Snapshot(ts=self.t, flows=flows, ifaces=ifs)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(appmod, "STARTUP_SAMPLE_SECS", 0.05)
    monkeypatch.setattr(DtopApp, "_interval", lambda self: 0.05)  # fast real-time ticks


async def warm(app, pilot, samples=12):
    for _ in range(200):
        await pilot.pause(0.05)
        if app.engine.samples >= samples:
            break
    await pilot.pause(0.1)


async def test_startup_message_then_table():
    app = DtopApp(FakeCollector(), refresh=10)
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.pause(0.01)
        assert not app.engine.ready
        assert app.query_one("#startup").display
        assert "ten seconds" in str(app.query_one("#startup").render())
        await warm(app, pilot)
        assert app.engine.ready
        table = app.query_one("#table", DataTable)
        assert table.display and table.row_count == 1  # beacon only; chatty filtered
        assert not app.query_one("#startup").display


async def test_toggle_all_and_sort():
    app = DtopApp(FakeCollector())
    async with app.run_test(size=(180, 50)) as pilot:
        await warm(app, pilot)
        await pilot.press("a")
        assert app.show_all and app.query_one("#table", DataTable).row_count == 2
        await pilot.press("a")
        assert not app.show_all and app.query_one("#table", DataTable).row_count == 1
        await pilot.press("o")
        assert app.sort == "risk"


async def test_refresh_keys_walk_choices():
    app = DtopApp(FakeCollector(), refresh=10)
    async with app.run_test(size=(180, 50)) as pilot:
        seen = []
        for _ in range(6):
            await pilot.press("plus")
            seen.append(int(app.refresh_secs))
        assert seen == [30, 60, 120, 300, 300, 300]
        seen = []
        for _ in range(8):
            await pilot.press("minus")
            seen.append(int(app.refresh_secs))
        assert seen == [120, 60, 30, 10, 5, 2, 2, 2]
        assert app.engine.refresh == 2.0


async def test_pause_stops_sampling():
    app = DtopApp(FakeCollector())
    async with app.run_test(size=(180, 50)) as pilot:
        await warm(app, pilot)
        await pilot.press("p")
        assert app.paused
        n = app.engine.samples
        await pilot.pause(0.5)
        assert app.engine.samples == n
        await pilot.press("p")
        assert not app.paused
        await pilot.pause(0.4)
        assert app.engine.samples > n


async def test_help_screen_has_tagline_and_desultory():
    from dtop.screens import HELP_TEXT
    assert "From the minds of IBM Bob & Daneyand" in HELP_TEXT
    assert "Occurring randomly, intermittently, or without a steady pattern" in HELP_TEXT
    app = DtopApp(FakeCollector())
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.press("h")
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("h")
        assert not isinstance(app.screen, HelpScreen)
        await pilot.press("question_mark")
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        assert len(app.screen_stack) == 1


async def test_net_screen_toggles():
    app = DtopApp(FakeCollector())
    async with app.run_test(size=(180, 50)) as pilot:
        await warm(app, pilot)
        await pilot.press("n")
        assert isinstance(app.screen, NetScreen)
        await pilot.pause(0.3)  # live update path
        await pilot.press("n")
        assert len(app.screen_stack) == 1


async def test_details_screen_for_selected_row():
    app = DtopApp(FakeCollector())
    async with app.run_test(size=(180, 50)) as pilot:
        await warm(app, pilot)
        await pilot.press("s")
        assert isinstance(app.screen, DetailScreen)
        assert app.screen.key == (4242, "beacon")
        await pilot.press("s")
        assert len(app.screen_stack) == 1


async def test_kill_dialog_cancel_with_k_and_esc_does_not_kill(monkeypatch):
    killed = []
    monkeypatch.setattr(killer, "kill_pid", lambda pid: killed.append(pid) or (True, "x"))
    app = DtopApp(FakeCollector())
    async with app.run_test(size=(180, 50)) as pilot:
        await warm(app, pilot)
        await pilot.press("k")
        assert isinstance(app.screen, KillScreen)
        await pilot.press("k")
        assert len(app.screen_stack) == 1
        await pilot.press("k")
        assert isinstance(app.screen, KillScreen)
        await pilot.press("escape")
        assert len(app.screen_stack) == 1
    assert killed == []


async def test_kill_dialog_enter_kills_selected(monkeypatch):
    killed = []
    import dtop.screens as scr
    monkeypatch.setattr(scr.killer, "kill_pid", lambda pid: killed.append(pid) or (True, "killed"))
    app = DtopApp(FakeCollector())
    async with app.run_test(size=(180, 50)) as pilot:
        await warm(app, pilot)
        await app.run_action("toggle_all")
        await pilot.press("k")
        assert isinstance(app.screen, KillScreen)
        ol = app.screen.query_one(OptionList)
        assert ol.option_count == 2
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert len(killed) == 1 and killed[0] in (4242, 4343)
        assert len(app.screen_stack) == 1


async def test_demo_mode_never_kills_or_looks_up_real_pids(monkeypatch):
    from dtop.collectors.demo import DemoCollector
    killed = []
    import dtop.screens as scr
    monkeypatch.setattr(scr.killer, "kill_pid", lambda pid: killed.append(pid) or (True, "x"))
    app = DtopApp(DemoCollector(), refresh=10)
    assert app.engine.synthetic and app.engine.info_fn(1, "x") is None
    async with app.run_test(size=(180, 50)) as pilot:
        await warm(app, pilot, samples=12)
        await app.run_action("toggle_all")
        await pilot.press("k")
        assert isinstance(app.screen, KillScreen)
        await pilot.press("enter")
        await pilot.pause(0.1)
    assert killed == []
