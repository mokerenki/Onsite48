"""Low-overhead process metrics with Prometheus exposition.

The registry is intentionally small and dependency-free. It is safe to use in
API processes and workers; production deployments can scrape /api/metrics and
aggregate by process labels in Prometheus.
"""
from __future__ import annotations
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping

@dataclass
class Histogram:
    count: int = 0
    total: float = 0.0
    buckets: dict[float, int] | None = None
    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        if self.buckets is not None:
            for bound in self.buckets:
                if value <= bound:
                    self.buckets[bound] += 1

class PlatformMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: defaultdict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self.gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self.histograms: dict[tuple[str, tuple[tuple[str, str], ...]], Histogram] = {}
        self.started_at = time.time()
    @staticmethod
    def _key(name: str, labels: Mapping[str, object] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
        return name, tuple(sorted((str(k), str(v)) for k, v in (labels or {}).items()))
    def inc(self, name: str, value: float = 1, labels: Mapping[str, object] | None = None) -> None:
        with self._lock: self.counters[self._key(name, labels)] += value
    def set(self, name: str, value: float, labels: Mapping[str, object] | None = None) -> None:
        with self._lock: self.gauges[self._key(name, labels)] = value
    def observe(self, name: str, value: float, labels: Mapping[str, object] | None = None, bounds: tuple[float, ...] = (0.1, 0.5, 1, 5, 15, 60)) -> None:
        key = self._key(name, labels)
        with self._lock:
            item = self.histograms.setdefault(key, Histogram(buckets={bound: 0 for bound in bounds}))
            item.observe(float(value))
    def text(self) -> str:
        lines = ["# HELP onsite48_metrics_uptime_seconds Process uptime.", "# TYPE onsite48_metrics_uptime_seconds gauge", f"onsite48_metrics_uptime_seconds {time.time() - self.started_at:.3f}"]
        with self._lock:
            for store, kind in ((self.counters, "counter"), (self.gauges, "gauge")):
                for (name, labels), value in store.items():
                    label_text = "{" + ",".join(f'{k}="{v.replace(chr(34), chr(39))}"' for k, v in labels) + "}" if labels else ""
                    lines.extend((f"# TYPE {name} {kind}", f"{name}{label_text} {value}"))
            for (name, labels), item in self.histograms.items():
                label_text = "{" + ",".join(f'{k}="{v.replace(chr(34), chr(39))}"' for k, v in labels) + "}" if labels else ""
                lines.append(f"# TYPE {name} histogram")
                for bound, count in (item.buckets or {}).items():
                    suffix = ("," if labels else "") + f'le="{bound}"'
                    lines.append(f"{name}_bucket{{{','.join(f'{k}="{v.replace(chr(34), chr(39))}"' for k, v in labels)}{suffix}}} {count}")
                lines.extend((f"{name}_sum{label_text} {item.total}", f"{name}_count{label_text} {item.count}"))
        return "\n".join(lines) + "\n"

metrics = PlatformMetrics()