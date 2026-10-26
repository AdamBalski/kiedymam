import asyncio
import json
import time
from uuid import uuid4

from app.logging_config import request_id_context


class BoundedAdmissionMiddleware:
    """Admit a fixed number of active requests and bound waiting requests."""

    def __init__(self, app, workers: int = 16, queue_size: int = 128):
        self.app = app
        self.workers = workers
        self.queue_size = queue_size
        self.condition = asyncio.Condition()
        self.active = 0
        self.queued = 0
        self.rejected = 0

    @staticmethod
    def _record(scope, status: int, duration: float, *, rejected: bool = False, active: int = 0, queued: int = 0):
        app = scope.get("app")
        metrics = getattr(getattr(app, "state", None), "metrics", None)
        if not metrics:
            return
        route = getattr(scope.get("route"), "path", None)
        attributes = {"http.request.method": scope.get("method", "GET"), "http.response.status_code": status}
        if route:
            attributes["http.route"] = route
        try:
            metrics.observe("http.server.request.duration", duration, attributes)
            metrics.set_gauge("http.server.active_requests", active)
            metrics.set_gauge("http.server.queue.depth", queued)
            if rejected:
                metrics.add_counter("http.server.admission.rejected", 1, {"http.response.status_code": status})
        except Exception:
            return

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        admitted = False
        started = time.monotonic()
        request_id = uuid4().hex
        request_context_token = request_id_context.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id
        async with self.condition:
            if self.active < self.workers:
                self.active += 1
                admitted = True
            elif self.queued < self.queue_size:
                self.queued += 1
                try:
                    await self.condition.wait_for(lambda: self.active < self.workers)
                finally:
                    self.queued -= 1
                self.active += 1
                admitted = True

        if not admitted:
            self.rejected += 1
            body = json.dumps({"detail": "Service is busy; retry shortly"}).encode("utf-8")
            try:
                await send({"type": "http.response.start", "status": 503, "headers": [
                    (b"content-type", b"application/json"), (b"retry-after", b"1"),
                    (b"content-length", str(len(body)).encode()), (b"x-request-id", request_id.encode()),
                ]})
                await send({"type": "http.response.body", "body": body})
                self._record(scope, 503, time.monotonic() - started, rejected=True,
                             active=self.active, queued=self.queued)
            finally:
                request_id_context.reset(request_context_token)
            return

        response_status = 500

        async def wrapped_send(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            async with self.condition:
                self.active -= 1
                self.condition.notify_all()
            self._record(scope, response_status, time.monotonic() - started,
                         active=self.active, queued=self.queued)
            request_id_context.reset(request_context_token)
