"""Command-line entry point."""
from __future__ import annotations

import argparse
import platform
import sys

import psutil

from dtop import TAGLINE, __version__
from dtop.collectors import CollectorError, pick_collector
from dtop.widgets import REFRESH_CHOICES


def _doctor(collector) -> int:
    print(f"dtop {__version__} - Desultory Top")
    print(f"python {platform.python_version()} on {platform.system()} {platform.release()}")
    print(f"psutil {psutil.__version__}")
    for line in collector.doctor():
        print(line)
    try:
        snap = collector.sample()
        procs = {(f.pid, f.name) for f in snap.flows}
        print(f"sample ok: {len(snap.flows)} flows from {len(procs)} processes, "
              f"{len(snap.ifaces)} interfaces, per-process bytes: {'yes' if snap.has_bytes else 'NO'}")
        for n in snap.notes:
            print(f"note: {n}")
    except CollectorError as exc:
        print(f"sample FAILED: {exc}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dtop",
        description="dtop (Desultory Top): find processes with very infrequent network activity, "
                    "using a self-adjusting sliding window.",
        epilog=TAGLINE,
    )
    ap.add_argument("-r", "--refresh", type=int, default=10, choices=REFRESH_CHOICES,
                    help="sampling interval in seconds (default: 10; change live with - / +)")
    ap.add_argument("--demo", action="store_true", help="run with synthetic data")
    ap.add_argument("--doctor", action="store_true", help="check platform capabilities and exit")
    ap.add_argument("--backend", choices=("auto", "psutil"), default="auto",
                    help="force the generic psutil backend (default: best for this OS)")
    ap.add_argument("-V", "--version", action="version", version=f"dtop {__version__}")
    args = ap.parse_args(argv)

    collector = pick_collector(demo=args.demo, force=None if args.backend == "auto" else "psutil")
    if args.doctor:
        return _doctor(collector)

    from dtop.app import DtopApp
    DtopApp(collector, refresh=args.refresh).run()
    print(TAGLINE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
