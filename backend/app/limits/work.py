import asyncio
from collections import deque
import time


class WorkLimits:
    """Process-wide bounds for expensive asynchronous work classes."""

    def __init__(self) -> None:
        self.umb_downloads = asyncio.Semaphore(1)
        self.xlsx_parses = asyncio.Semaphore(1)
        self._rate_lock = asyncio.Lock()
        self._download_starts: deque[float] = deque()
        self._last_download = 0.0

    async def wait_for_umb_slot(self) -> None:
        """Enforce a 15-second gap and no more than 100 attempts in a rolling hour."""
        async with self._rate_lock:
            while True:
                now = time.monotonic()
                while self._download_starts and now - self._download_starts[0] >= 3600:
                    self._download_starts.popleft()
                spacing_wait = max(0.0, 15 - (now - self._last_download)) if self._last_download else 0.0
                hourly_wait = max(0.0, 3600 - (now - self._download_starts[0])) if len(self._download_starts) >= 100 else 0.0
                wait = max(spacing_wait, hourly_wait)
                if wait <= 0:
                    started = time.monotonic()
                    self._last_download = started
                    self._download_starts.append(started)
                    return
                await asyncio.sleep(wait)
