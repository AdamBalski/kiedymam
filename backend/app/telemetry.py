"""Application-owned sampling callbacks for the generic metrics store."""

import os
from pathlib import Path
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.metrics.store import MetricsStore

class ApplicationMetricsSampler:
    """Sample application and container gauges into the generic SQLite store."""

    def __init__(self, store: "MetricsStore"):
        self.store = store
        self._last_oom_kills: int | None = None

    def collect(self, *, queue_stats: dict[str, int], source_poll_time: float | None,
                db_paths: list[str]) -> None:
        usage = os.times()
        self.store.set_gauge("process.cpu.seconds", usage.user + usage.system)
        try:
            with open("/proc/self/statm", encoding="ascii") as statm:
                resident_pages = int(statm.read().split()[1])
            self.store.set_gauge("process.memory.rss", resident_pages * os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError, IndexError):
            pass
        for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
            try:
                value = Path(path).read_text().strip()
                if value != "max":
                    self.store.set_gauge("container.memory.limit", float(value))
                    break
            except (OSError, ValueError):
                continue
        try:
            quota = Path("/sys/fs/cgroup/cpu.max").read_text().split()
            if quota[0] != "max":
                self.store.set_gauge("container.cpu.quota.cores", float(quota[0]) / float(quota[1]))
        except (OSError, ValueError, IndexError, ZeroDivisionError):
            pass
        try:
            oom_kills = next(
                int(line.split()[1]) for line in Path("/sys/fs/cgroup/memory.events").read_text().splitlines()
                if line.startswith("oom_kill ")
            )
            if self._last_oom_kills is not None and oom_kills > self._last_oom_kills:
                self.store.add_counter("container.memory.oom_kill.count", oom_kills - self._last_oom_kills)
            self._last_oom_kills = oom_kills
        except (OSError, ValueError, IndexError, StopIteration):
            pass

        self.store.set_gauge("background.job.queue.depth", float(queue_stats.get("waiting", 0)),
                             {"queue.name": "background"})
        self.store.set_gauge("background.job.active", float(queue_stats.get("active", 0)),
                             {"queue.name": "background"})
        self.store.set_gauge("background.job.queue.oldest_waiting",
                             float(queue_stats.get("oldest_waiting_seconds", 0)),
                             {"queue.name": "background"})
        if source_poll_time:
            self.store.set_gauge("umb.source.poll.age", max(0, time.time() - source_poll_time))
        for label, path in zip(("application", "metrics"), db_paths):
            try:
                size = Path(path).stat().st_size
            except OSError:
                size = 0
            self.store.set_gauge("database.file.size", float(size), {"operation.name": label})
