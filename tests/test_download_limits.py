import unittest
from unittest.mock import patch

from app.calendar import source


class FakeResponse:
    status_code = 200
    headers = {}

    def __init__(self):
        self.read_past_limit_chunk = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def raise_for_status(self):
        pass

    async def aiter_bytes(self):
        yield b"x" * (source.MAX_XLSX_BYTES + 1)
        self.read_past_limit_chunk = True
        yield b"this chunk should not be consumed"


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.response = FakeResponse()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def stream(self, *args, **kwargs):
        return self.response


class DownloadLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversize_workbook_is_logged_and_stream_stops_at_first_oversize_chunk(self):
        client = FakeClient()
        with patch.object(source.httpx, "AsyncClient", return_value=client):
            with self.assertLogs("app.calendar.source", level="WARNING") as captured:
                with self.assertRaisesRegex(ValueError, "5 MiB limit"):
                    await source._download("https://www.umb.edu.pl/plan.xlsx")

        self.assertFalse(client.response.read_past_limit_chunk)
        self.assertIn(str(source.MAX_XLSX_BYTES), captured.output[0])
        self.assertIn("https://www.umb.edu.pl/plan.xlsx", captured.output[0])


if __name__ == "__main__":
    unittest.main()
