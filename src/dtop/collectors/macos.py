"""macOS collector: ``nettop`` gives true per-process, per-flow byte counters."""
from __future__ import annotations

import csv
import shutil
import subprocess

from dtop.collectors.base import Collector, CollectorError, now, read_ifaces
from dtop.models import Flow, Snapshot

CMD = ["nettop", "-x", "-n", "-L", "1", "-J", "interface,state,bytes_in,bytes_out"]


def _int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return 0


def parse_nettop(text: str) -> list[Flow]:
    """Parse ``nettop -x -L 1`` CSV. Process rows are followed by their flows."""
    flows: list[Flow] = []
    pid, pname = 0, ""
    for row in csv.reader(text.splitlines()):
        if len(row) < 5 or not row[0]:
            continue
        label = row[0]
        head = label.split(" ", 1)[0]
        if head.startswith(("tcp", "udp")) and "<->" in label:
            if row[3] == "" and row[4] == "":
                continue  # unconnected wildcard placeholder
            local, _, remote = label.split(" ", 1)[1].partition("<->")
            flows.append(Flow(
                pid=pid, name=pname, proto=head, local=local, remote=remote,
                iface=row[1], state=row[2], bytes_in=_int(row[3]), bytes_out=_int(row[4]),
            ))
        else:
            base, _, num = label.rpartition(".")
            if num.isdigit():
                pid, pname = int(num), base
            else:
                pid, pname = 0, label
    return flows


class NettopCollector(Collector):
    name = "macos-nettop"
    description = "per-process / per-flow byte counters via nettop (no root needed)"

    def __init__(self) -> None:
        if not shutil.which("nettop"):
            raise CollectorError("nettop not found")

    def sample(self) -> Snapshot:
        try:
            out = subprocess.run(CMD, capture_output=True, text=True, timeout=20, check=True).stdout
        except (subprocess.SubprocessError, OSError) as exc:
            raise CollectorError(f"nettop failed: {exc}") from exc
        return Snapshot(ts=now(), flows=parse_nettop(out), ifaces=read_ifaces(), has_bytes=True)
