import asyncio
import unittest

from app.jobs.queue import InMemoryJobQueue, QueueFullError


class JobQueueTests(unittest.TestCase):
    def test_capacity_and_deduplication_include_running_jobs(self):
        async def scenario():
            started = asyncio.Event()
            release = asyncio.Event()

            async def handler(_payload):
                started.set()
                await release.wait()
                return {"ok": True}

            queue = InMemoryJobQueue({"work": handler}, workers=1, max_pending=1)
            await queue.start()
            try:
                first = await queue.enqueue("work", {}, trigger="test", dedupe_key="same")
                await asyncio.wait_for(started.wait(), timeout=1)
                duplicate = await queue.enqueue("work", {}, trigger="test", dedupe_key="same")
                self.assertEqual(first["job_id"], duplicate["job_id"])
                await queue.enqueue("work", {}, trigger="test", dedupe_key="pending")
                with self.assertRaises(QueueFullError):
                    await queue.enqueue("work", {}, trigger="test")
            finally:
                release.set()
                await queue.stop()

        asyncio.run(scenario())

    def test_worker_count_is_bounded(self):
        async def scenario():
            active = 0
            maximum = 0
            lock = asyncio.Lock()
            release = asyncio.Event()

            async def handler(_payload):
                nonlocal active, maximum
                async with lock:
                    active += 1
                    maximum = max(maximum, active)
                await release.wait()
                async with lock:
                    active -= 1

            queue = InMemoryJobQueue({"work": handler}, workers=2, max_pending=8)
            await queue.start()
            try:
                for _ in range(6):
                    await queue.enqueue("work", {}, trigger="test")
                for _ in range(50):
                    if maximum == 2:
                        break
                    await asyncio.sleep(.01)
                self.assertEqual(maximum, 2)
            finally:
                release.set()
                await queue.stop()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
