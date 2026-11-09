from __future__ import annotations

import os
import subprocess
import sys

import psutil
import pytest

from dtop import risk
from dtop.collectors.macos import parse_nettop
from dtop.collectors.psutil_based import parse_ss
from dtop.fmt import ago, human_bytes, human_dur, human_rate, sparkline, split_endpoint
from dtop.killer import kill_pid
from dtop.procinfo import ProcInfo

NETTOP = """\
,interface,state,bytes_in,bytes_out,
syslogd.572,,,0,11485,
udp4 *:63932<->*:*,lo0,,0,11485,
Microsoft Outlo.3434,,,524638,186133,
tcp4 192.168.1.20:58927<->52.96.191.242:443,en7,Established,524638,186133,
airportd.682,,,0,0,
udp4 *:*<->*:*,,,,
Example Agent.3450,,,10,20,
tcp6 fe80::1%en7.59006<->fe80::2%en7.52380,en7,Established,10,20,
"""


def test_parse_nettop():
    flows = parse_nettop(NETTOP)
    assert len(flows) == 3  # wildcard placeholder skipped
    ol = [f for f in flows if f.pid == 3434][0]
    assert ol.name == "Microsoft Outlo" and ol.iface == "en7"
    assert (ol.bytes_in, ol.bytes_out) == (524638, 186133)
    assert ol.local == "192.168.1.20:58927" and ol.remote == "52.96.191.242:443"
    assert {f.pid for f in flows} == {572, 3434, 3450}


SS = (
    'ESTAB 0 0 10.0.0.2:44321 93.184.216.34:443 users:(("curl",pid=4242,fd=3)) '
    'cubic wscale:7,7 bytes_sent:1500 bytes_acked:1500 bytes_received:9000 segs_out:5\n'
    'ESTAB 0 0 10.0.0.2:44322 1.1.1.1:443 cubic bytes_received:1\n'  # no owner: skipped
)


def test_parse_ss():
    d = parse_ss(SS)
    assert d == {(4242, "10.0.0.2:44321", "93.184.216.34:443"): (9000, 1500)}


def test_formatting():
    assert human_bytes(0) == "0 B"
    assert human_bytes(1536) == "1.5 KB"
    assert human_bytes(5 * 1024**3) == "5.0 GB"
    assert human_bytes(None) == "n/a"
    assert human_rate(2048) == "2.0 KB/s"
    assert human_dur(7500) == "2h 05m"
    assert human_dur(59) == "59s" and human_dur(61) == "1m 01s"
    assert ago(None) == "never" and ago(0.2) == "now" and ago(90) == "1m 30s ago"
    assert split_endpoint("1.2.3.4:443") == ("1.2.3.4", "443")
    assert split_endpoint("[::1]:80") == ("::1", "80")
    assert len(sparkline([1, 2, 3], 10)) == 10 and sparkline([], 4) == "▁▁▁▁"


def test_is_public():
    assert risk.is_public("8.8.8.8:53")
    assert not risk.is_public("192.168.1.1:80")
    assert not risk.is_public("127.0.0.1:1")
    assert not risk.is_public("224.0.0.251:5353")
    assert not risk.is_public("*:*")


def test_risk_high_for_classic_beacon():
    info = ProcInfo(pid=1, name="python3", exe="/tmp/.x/python3", user="bob")
    score, why = risk.assess(
        name="python3", pattern="periodic ~1m", remote="45.9.148.108:4444",
        total_in=1000, total_out=5_000_000, bytes_known=True, info=info,
    )
    assert score >= 70 and risk.level(score) == "HIGH"
    assert len(why) >= 4


def test_risk_low_for_quiet_local_service():
    score, why = risk.assess(
        name="mdnsd", pattern="irregular", remote="224.0.0.251:5353",
        total_in=500, total_out=500, bytes_known=True,
        info=ProcInfo(pid=1, name="mdnsd", exe="/usr/sbin/mdnsd", user="root"),
    )
    assert risk.level(score) == "OK" and why == []


def test_risk_windows_exe_name():
    score, _ = risk.assess(name="PowerShell.exe", pattern="single", remote="",
                           total_in=0, total_out=0, bytes_known=False, info=None)
    assert score == 20


def test_kill_refuses_dangerous_pids():
    assert kill_pid(1)[0] is False
    assert kill_pid(0)[0] is False
    ok, msg = kill_pid(os.getpid())
    assert not ok and "itself" in msg


@pytest.mark.skipif(os.name == "nt", reason="uses sleep binary")
def test_kill_actually_kills():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        ok, msg = kill_pid(p.pid)
        assert ok, msg
        p.wait(timeout=5)  # reaped by psutil; exit code is not meaningful here
    finally:
        if p.poll() is None:
            p.kill()
    assert not psutil.pid_exists(p.pid)


def test_kill_missing_pid_is_ok():
    ok, msg = kill_pid(4_000_000)
    assert ok and "gone" in msg


def test_silent_process_scores_zero_even_with_public_peer():
    score, why = risk.assess(
        name="python3", pattern="silent", remote="8.8.8.8:443",
        total_in=0, total_out=0, bytes_known=True,
        info=ProcInfo(pid=1, name="python3", exe="/tmp/x", user="bob"),
    )
    assert score == 0 and why == []
