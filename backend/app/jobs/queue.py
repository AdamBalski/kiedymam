from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import heapq
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from app.logging_config import job_id_context

LOG = logging.getLogger(__name__)
JobHandler = Callable[[dict[str, Any]], Awaitable[Any]]


class QueueFullError(RuntimeError):
    pass


@dataclass(order=True)
class _QueuedJob:
    run_at: float
    sequence: int
    job_id: str = field(compare=False)
    kind: str = field(compare=False)
    trigger: str = field(compare=False)
    payload: dict[str, Any] = field(compare=False)
    dedupe_key: str | None = field(compare=False, default=None)
    created_monotonic: float = field(compare=False, default=0)


class InMemoryJobQueue:
    """Bounded, process-local queue; pending jobs intentionally do not survive restarts."""

    def __init__(self, handlers: dict[str, JobHandler], *, workers: int = 4, max_pending: int = 512, metrics=None):
        self.handlers = handlers
        self.worker_count = workers
        self.max_pending = max_pending
        self.metrics = metrics
        self._condition: asyncio.Condition | None = None
        self._pending: list[_QueuedJob] = []
        self._workers: list[asyncio.Task] = []
        self._jobs: dict[str, dict[str, Any]] = {}
        self._dedupe: dict[str, str] = {}
        self._sequence = 0
        self._stopping = False
        self.rejected = 0

    async def start(self) -> None:
        if self._workers:
            return
        self._condition = asyncio.Condition()
        self._stopping = False
        self._workers = [asyncio.create_task(self._worker(index), name=f"job-worker-{index}")
                         for index in range(self.worker_count)]

    async def stop(self) -> None:
        if not self._workers:
            return
        self._stopping = True
        assert self._condition is not None
        async with self._condition:
            self._condition.notify_all()
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._pending.clear()
        self._dedupe.clear()

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        trigger: str,
        delay_seconds: float = 0,
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        if kind not in self.handlers:
            raise ValueError(f"Unknown background job kind: {kind}")
        assert self._condition is not None, "job queue has not started"
        async with self._condition:
            if dedupe_key and dedupe_key in self._dedupe:
                existing = self._jobs.get(self._dedupe[dedupe_key])
                if existing:
                    return dict(existing)
            if len(self._pending) >= self.max_pending:
                self.rejected += 1
                if self.metrics:
                    self.metrics.add_counter("background.job.queue.rejected", attributes={"queue.name": kind})
                raise QueueFullError("Background work queue is full")
            job_id = str(uuid.uuid4())
            enqueued_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            now_monotonic = time.monotonic()
            job = _QueuedJob(now_monotonic + max(0, delay_seconds), self._sequence,
                             job_id, kind, trigger, dict(payload), dedupe_key, now_monotonic)
            self._sequence += 1
            heapq.heappush(self._pending, job)
            if dedupe_key:
                self._dedupe[dedupe_key] = job_id
            self._jobs[job_id] = {
                "job_id": job_id, "kind": kind, "trigger": trigger, "status": "queued",
                "enqueued_at": enqueued_at, "started_at": None, "finished_at": None,
                "error": None, "owner_user_id": payload.get("user_id"),
                "integration_id": payload.get("integration_id"),
            }
            self._trim_history()
            self._condition.notify_all()
            if self.metrics:
                attributes = {"queue.name": kind, "sync.trigger": trigger, "outcome": "queued"}
                self.metrics.add_counter("background.job.count", attributes=attributes)
                self.metrics.set_gauge("background.job.queue.depth", len(self._pending), {"queue.name": kind})
            return dict(self._jobs[job_id])

    def enqueue_from_scheduler(self, loop: asyncio.AbstractEventLoop, kind: str, payload: dict[str, Any], *,
                               trigger: str, dedupe_key: str | None = None) -> None:
        future = asyncio.run_coroutine_threadsafe(
            self.enqueue(kind, payload, trigger=trigger, dedupe_key=dedupe_key), loop
        )

        def report(result):
            try:
                result.result()
            except QueueFullError:
                LOG.warning("Scheduled background job rejected because queue is full kind=%s", kind)
            except Exception:
                LOG.exception("Could not enqueue scheduled background job kind=%s", kind)

        future.add_done_callback(report)

    def stats(self) -> dict[str, float | int]:
        waiting = len(self._pending)
        active = sum(1 for job in self._jobs.values() if job["status"] == "running")
        oldest_waiting = max(
            (time.monotonic() - job.created_monotonic for job in self._pending),
            default=0.0,
        )
        return {"active": active, "waiting": waiting, "rejected": self.rejected,
                "workers": self.worker_count, "capacity": self.max_pending,
                "oldest_waiting_seconds": oldest_waiting}

    async def cancel(self, predicate: Callable[[str, dict[str, Any]], bool]) -> int:
        assert self._condition is not None
        async with self._condition:
            kept = []
            removed = 0
            for queued in self._pending:
                if predicate(queued.kind, queued.payload):
                    removed += 1
                    if queued.dedupe_key:
                        self._dedupe.pop(queued.dedupe_key, None)
                    self._jobs[queued.job_id].update(status="cancelled", finished_at=datetime.now(timezone.utc).isoformat())
                else:
                    kept.append(queued)
            self._pending = kept
            heapq.heapify(self._pending)
            self._condition.notify_all()
            return removed

    async def _worker(self, worker_index: int) -> None:
        assert self._condition is not None
        while not self._stopping:
            async with self._condition:
                while not self._pending and not self._stopping:
                    await self._condition.wait()
                if self._stopping:
                    return
                wait_seconds = self._pending[0].run_at - time.monotonic()
                if wait_seconds > 0:
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=wait_seconds)
                    except asyncio.TimeoutError:
                        pass
                    continue
                job = heapq.heappop(self._pending)
                record = self._jobs[job.job_id]
                record.update(status="running", started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                if self.metrics:
                    self.metrics.set_gauge("background.job.queue.depth", len(self._pending), {"queue.name": job.kind})
                    self.metrics.set_gauge("background.job.active", self.stats()["active"], {"queue.name": job.kind})
                    self.metrics.add_counter("background.job.count", attributes={
                        "queue.name": job.kind, "sync.trigger": job.trigger, "outcome": "started",
                    })
                    self.metrics.observe("background.job.wait.duration", max(0, time.monotonic() - job.created_monotonic),
                                         {"queue.name": job.kind, "sync.trigger": job.trigger})
            started = time.monotonic()
            job_context_token = job_id_context.set(job.job_id)
            try:
                handler_payload = {**job.payload, "_trigger": job.trigger, "_job_id": job.job_id}
                result = await self.handlers[job.kind](handler_payload)
                record.update(status="succeeded", result=result)
                if self.metrics:
                    self.metrics.add_counter("background.job.count", attributes={"queue.name": job.kind,
                                         "sync.trigger": job.trigger, "outcome": "success"})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.exception("Background job failed kind=%s job_id=%s worker=%d", job.kind, job.job_id, worker_index)
                record.update(status="failed", error=type(exc).__name__)
                if self.metrics:
                    self.metrics.add_counter("background.job.count", attributes={"queue.name": job.kind,
                                         "sync.trigger": job.trigger, "outcome": "failure"})
            finally:
                record["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                record["duration_seconds"] = round(time.monotonic() - started, 3)
                if self.metrics:
                    self.metrics.observe("background.job.duration", time.monotonic() - started,
                                         {"queue.name": job.kind, "sync.trigger": job.trigger,
                                          "outcome": record["status"]})
                    self.metrics.set_gauge("background.job.active", self.stats()["active"], {"queue.name": job.kind})
                self._trim_history()
                if job.dedupe_key:
                    self._dedupe.pop(job.dedupe_key, None)
                job_id_context.reset(job_context_token)

    def _trim_history(self) -> None:
        if len(self._jobs) <= 2048:
            return
        finished = [key for key, value in self._jobs.items() if value["status"] not in ("queued", "running")]
        for key in finished[:len(self._jobs) - 2048]:
            self._jobs.pop(key, None)
