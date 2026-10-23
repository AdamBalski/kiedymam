import asyncio
import json


class BoundedAdmissionMiddleware:
    """Admit a fixed number of active requests and bound waiting requests."""

    def __init__(self, app, workers: int = 16, queue_size: int = 128):
        self.app = app
        self.workers = workers
        self.queue_size = queue_size
        self.condition = asyncio.Condition()
        self.active = 0
        self.queued = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        admitted = False
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
            body = json.dumps({"detail": "Service is busy; retry shortly"}).encode("utf-8")
            await send({"type": "http.response.start", "status": 503, "headers": [(b"content-type", b"application/json"), (b"retry-after", b"1"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return

        try:
            await self.app(scope, receive, send)
        finally:
            async with self.condition:
                self.active -= 1
                self.condition.notify_all()
