"""Collector selection per platform."""
from __future__ import annotations

import sys

from dtop.collectors.base import Collector, CollectorError


def pick_collector(demo: bool = False, force: str | None = None) -> Collector:
    if demo or force == "demo":
        from dtop.collectors.demo import DemoCollector
        return DemoCollector()
    if force != "psutil" and sys.platform == "darwin":
        try:
            from dtop.collectors.macos import NettopCollector
            return NettopCollector()
        except CollectorError:
            pass
    from dtop.collectors.psutil_based import PsutilCollector
    return PsutilCollector()


__all__ = ["Collector", "CollectorError", "pick_collector"]
