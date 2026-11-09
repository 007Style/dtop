from __future__ import annotations

import pytest

from dtop.engine import Engine
from dtop.models import Flow, IfaceCounters, Snapshot


def flow(pid=1, name="p", rin=0, rout=0, remote="8.8.8.8:443", iface="en0", local="10.0.0.2:5000"):
    return Flow(pid, name, "tcp4", local, remote, iface, "Established", rin, rout)


def snap(ts, flows, ifaces=None, has_bytes=True):
    return Snapshot(ts=ts, flows=flows, ifaces=ifaces or {}, has_bytes=has_bytes)


def make_engine(refresh=10.0):
    return Engine(refresh=refresh, info_fn=lambda p, n: None, pid_alive=lambda p: True)


def run(engine, beats, total_secs, step=10.0, name="beacon", pid=7):
    """Feed samples; process bytes grow only at the times in ``beats``."""
    t, rin, rout = 1000.0, 0, 0
    pending = sorted(beats)
    end = t + total_secs
    while t <= end:
        while pending and pending[0] + 1000.0 <= t:
            pending.pop(0)
            rin += 500
            rout += 100
        engine.ingest(snap(t, [flow(pid, name, rin, rout)]))
        t += step
    return engine


def test_first_sample_is_baseline_not_activity():
    e = make_engine()
    e.ingest(snap(1000.0, [flow(rin=999_999, rout=5)]))
    assert e.procs[(1, "p")].last_active is None
    assert e.procs[(1, "p")].total_in == 0


def test_delta_and_episode_bytes():
    e = make_engine()
    e.ingest(snap(1000.0, [flow(rin=100, rout=10)]))
    e.ingest(snap(1010.0, [flow(rin=600, rout=60)]))
    t = e.procs[(1, "p")]
    assert (t.total_in, t.total_out) == (500, 50)
    assert (t.ep_in, t.ep_out) == (500, 50)
    assert len(t.episodes) == 1
    e.ingest(snap(1020.0, [flow(rin=600, rout=60)]))
    assert t.active_prev is False


def test_consecutive_active_samples_merge_into_one_episode():
    e = make_engine()
    for i, ts in enumerate((1000.0, 1010.0, 1020.0, 1030.0)):
        e.ingest(snap(ts, [flow(rin=i * 100, rout=0)]))
    t = e.procs[(1, "p")]
    assert len(t.episodes) == 1
    assert t.ep_in == 300
    assert t.total_in == 300


def test_new_episode_resets_last_comm_bytes():
    e = make_engine()
    e.ingest(snap(1000.0, [flow(rin=0)]))
    e.ingest(snap(1010.0, [flow(rin=100)]))
    e.ingest(snap(1020.0, [flow(rin=100)]))
    e.ingest(snap(1030.0, [flow(rin=350)]))
    t = e.procs[(1, "p")]
    assert len(t.episodes) == 2
    assert t.ep_in == 250 and t.total_in == 350


def test_counter_reset_is_handled():
    e = make_engine()
    e.ingest(snap(1000.0, [flow(rin=1000)]))
    e.ingest(snap(1010.0, [flow(rin=40)]))
    assert e.procs[(1, "p")].total_in == 40


def test_new_flow_counts_as_activity():
    e = make_engine()
    e.ingest(snap(1000.0, [flow(pid=1, rin=5)]))
    e.ingest(snap(1010.0, [flow(pid=1, rin=5), flow(pid=1, rin=77, local="10.0.0.2:6000")]))
    assert e.procs[(1, "p")].total_in == 77


def test_no_byte_counters_new_flow_is_the_signal():
    e = make_engine()
    nf = lambda **kw: Flow(3, "x", "tcp4", kw.get("local", "a:1"), "9.9.9.9:80", "en0", "ESTABLISHED", None, None)
    e.ingest(snap(1000.0, [nf()], has_bytes=False))
    e.ingest(snap(1010.0, [nf()], has_bytes=False))
    assert e.procs[(3, "x")].last_active is None
    e.ingest(snap(1020.0, [nf(), nf(local="a:2")], has_bytes=False))
    assert e.procs[(3, "x")].last_active == 1020.0
    assert e.procs[(3, "x")].bytes_known is False


def test_window_floor_and_refresh_scaling():
    assert make_engine(10).window == 30.0
    assert make_engine(120).window == 360.0


def test_window_adapts_to_slow_rhythms():
    # beacon every 2 hours for 12 hours, sampled every 5 minutes
    e = make_engine(refresh=300)
    beats = [h * 7200 for h in range(1, 7)]
    run(e, beats, total_secs=12 * 3600, step=300)
    assert e.window >= 4 * 7200 - 1
    d, tracked = e.counts()
    assert d == 1 and tracked == 1


def test_window_stays_small_for_fast_rhythms():
    e = make_engine(refresh=10)
    beats = [i * 60 for i in range(1, 10)]
    run(e, beats, total_secs=600, step=10)
    assert e.window <= 600
    assert e._window_cache >= 30


def test_continuous_talker_is_filtered_but_shown_with_all():
    e = make_engine()
    t, rin = 1000.0, 0
    for _ in range(40):
        rin += 1000
        e.ingest(snap(t, [flow(1, "stream", rin, 0), flow(2, "rare", 0, 0, remote="1.1.1.1:53") ]))
        t += 10
    names = {r.name for r in e.rows()}
    assert "stream" not in names
    assert "stream" in {r.name for r in e.rows(show_all=True)}


def test_process_going_cold_leaves_infrequent_view():
    e = make_engine()
    run(e, [100, 200, 300], total_secs=400, step=10)
    assert len(e.rows()) == 1
    t = 1000.0 + 410
    for _ in range(300):
        e.ingest(snap(t, [flow(7, "beacon", 1500, 300)]))
        t += 10
    assert e.rows() == []


def test_periodic_pattern_and_sorting():
    e = make_engine()
    run(e, [i * 60 for i in range(1, 12)], total_secs=700, step=10)
    r = e.rows()[0]
    assert r.pattern.startswith("periodic")
    assert r.episodes >= 8
    assert e.rows(sort="risk") and e.rows(sort="total") and e.rows(sort="name")


def test_exited_state():
    alive = {"v": True}
    e = Engine(info_fn=lambda p, n: None, pid_alive=lambda p: alive["v"])
    e.ingest(snap(1000.0, [flow(rin=0)]))
    e.ingest(snap(1010.0, [flow(rin=50)]))
    e.ingest(snap(1020.0, []))
    alive["v"] = False
    assert e.rows()[0].state == "EXITED"


def test_startup_gate():
    e = make_engine()
    e.ingest(snap(1000.0, [flow()]))
    assert not e.ready
    e.ingest(snap(1004.0, [flow()]))
    assert not e.ready
    e.ingest(snap(1010.0, [flow()]))
    assert e.ready


def test_interface_totals_and_rates():
    e = make_engine()
    mk = lambda i, o: {
        "en0": IfaceCounters("en0", i, o, ipv4=["10.0.0.2"]),
        "lo0": IfaceCounters("lo0", i * 9, o * 9),
    }
    e.ingest(snap(1000.0, [flow()], mk(1000, 500)))
    e.ingest(snap(1010.0, [flow()], mk(21_000, 10_500)))
    rows = {r.name: r for r in e.iface_rows()}
    assert rows["en0"].total_in == 20_000 and rows["en0"].total_out == 10_000
    assert rows["en0"].rate_in == pytest.approx(2000.0)
    assert rows["en0"].procs == ["p"] and rows["en0"].nflows == 1
    assert e.history[-1][1] == pytest.approx(2000.0)  # loopback excluded


def test_remote_and_iface_tracking():
    e = make_engine()
    e.ingest(snap(1000.0, [flow(rin=0, iface="en7")]))
    e.ingest(snap(1010.0, [flow(rin=10, iface="en7")]))
    r = e.rows(show_all=True)[0]
    assert r.ifaces == ["en7"] and r.remotes == ["8.8.8.8:443"]
    assert r.iface_detail[0][0] == "en7" and r.iface_detail[0][1] == 10
