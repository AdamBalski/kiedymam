import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from google.auth.exceptions import RefreshError

from app.calendar.service import CalendarService
from app.calendar.parsers.registry import PARSER_CACHE_VERSION
from app.db.database import Database
from app.db.repository import Repository


class CalendarServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database = Database(os.path.join(self.tempdir.name, "test.sqlite3"))
        database.initialize()
        with database.transaction(immediate=True) as db:
            db.execute(
                "INSERT INTO users(id,google_sub,email,display_name,created_at) VALUES(?,?,?,?,?)",
                ("user-1", "google-1", "student@example.edu", "Student", "2026-10-23T10:00:00+00:00"),
            )
        self.repository = Repository(database)
        self.integration = self.repository.create_integration(
            user_id="user-1",
            source_url="https://www.umb.edu.pl/schedule.xlsx",
            group_number=1,
            calendar_id="calendar-1",
            calendar_name="UMB Fizjoterapia — grupa 6 nr 1",
        )
        self.service = CalendarService(self.repository, crypto=None)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_revoked_google_authorization_does_not_block_local_integration_deletion(self):
        with patch("app.calendar.service.gcal.delete_calendar", side_effect=RefreshError("invalid_grant")):
            calendar_deleted = self.service.delete_integration(self.integration)

        self.assertFalse(calendar_deleted)
        self.assertIsNone(self.repository.get_integration(self.integration["id"], "user-1"))

    def test_unchanged_fresh_source_reuses_parsed_cache_without_parsing_or_reconciling(self):
        parsed = {"events": [], "changelog": [], "_parser_version": PARSER_CACHE_VERSION}
        self.repository.upsert_source_cache(self.integration["source_url"], "same-sha", parsed)

        async def exercise():
            with (
                patch(
                    "app.calendar.service.download_and_hash",
                    new=AsyncMock(return_value=("same-sha", b"unchanged workbook")),
                ),
                patch("app.calendar.service.parse_downloaded_source", new=AsyncMock()) as parse,
                patch.object(self.service, "reconcile") as reconcile,
            ):
                result = await self.service.refresh_source(self.integration["source_url"])

            self.assertEqual(result, (False, False, parsed))
            parse.assert_not_awaited()
            reconcile.assert_not_called()

        asyncio.run(exercise())

    def test_manual_sync_with_unchanged_sha_does_not_reconcile(self):
        parsed = {"events": [], "changelog": [], "_parser_version": PARSER_CACHE_VERSION}
        self.repository.upsert_source_cache(self.integration["source_url"], "same-sha", parsed)

        async def exercise():
            with (
                patch(
                    "app.calendar.service.download_and_hash",
                    new=AsyncMock(return_value=("same-sha", b"unchanged workbook")),
                ),
                patch("app.calendar.service.parse_downloaded_source", new=AsyncMock()) as parse,
                patch.object(self.service, "reconcile") as reconcile,
            ):
                result = await self.service.sync_integration(self.integration, force_remote_read=True)

            self.assertEqual(
                result,
                {
                    "source_changed": False,
                    "reconciled": "none",
                    "added_or_updated": 0,
                    "removed": 0,
                },
            )
            parse.assert_not_awaited()
            reconcile.assert_not_called()

        asyncio.run(exercise())

    def test_changed_source_is_parsed_and_reconciled(self):
        cached = {"events": [], "changelog": [], "_parser_version": PARSER_CACHE_VERSION}
        self.repository.upsert_source_cache(self.integration["source_url"], "old-sha", cached)
        parsed = {"events": [{"event_key": "new"}], "changelog": []}

        async def exercise():
            with (
                patch(
                    "app.calendar.service.download_and_hash",
                    new=AsyncMock(return_value=("new-sha", b"changed workbook")),
                ),
                patch(
                    "app.calendar.service.parse_downloaded_source",
                    new=AsyncMock(return_value=parsed),
                ) as parse,
                patch.object(self.service, "reconcile") as reconcile,
            ):
                result = await self.service.refresh_source(self.integration["source_url"])

            self.assertEqual(result, (True, True, {**parsed, "_parser_version": PARSER_CACHE_VERSION}))
            parse.assert_awaited_once()
            reconcile.assert_called_once()
            cache = self.repository.get_source_cache(self.integration["source_url"])
            self.assertEqual(cache["content_sha256"], "new-sha")
            self.assertEqual(cache["parsed"]["_parser_version"], PARSER_CACHE_VERSION)

        asyncio.run(exercise())

    def test_manual_source_refresh_marks_only_requested_integration_as_manual(self):
        with self.repository.database.transaction(immediate=True) as db:
            db.execute(
                "INSERT INTO users(id,google_sub,email,display_name,created_at) VALUES(?,?,?,?,?)",
                ("user-2", "google-2", "other@example.edu", "Other", "2026-10-23T10:00:00+00:00"),
            )
        other = self.repository.create_integration(
            user_id="user-2", source_url=self.integration["source_url"], group_number=2,
            calendar_id="calendar-2", calendar_name="Other plan",
        )
        calls = []
        parsed = {"events": [], "changelog": []}

        async def exercise():
            with (
                patch("app.calendar.service.download_and_hash", new=AsyncMock(return_value=("new-hash", b"xlsx"))),
                patch("app.calendar.service.parse_downloaded_source", new=AsyncMock(return_value=parsed)),
                patch.object(
                    self.service, "reconcile",
                    side_effect=lambda integration, _schedule, **kwargs:
                        calls.append((integration["id"], kwargs["sync_trigger"])),
                ),
            ):
                await self.service.refresh_source(
                    self.integration["source_url"], self.integration["id"], "manual"
                )

        asyncio.run(exercise())
        self.assertEqual(dict(calls), {
            self.integration["id"]: "manual", other["id"]: "source_changed"
        })

    def test_integration_change_suppresses_only_the_edited_integration_notices(self):
        with self.repository.database.transaction(immediate=True) as db:
            db.execute(
                "INSERT INTO users(id,google_sub,email,display_name,created_at) VALUES(?,?,?,?,?)",
                ("user-2", "google-2", "other@example.edu", "Other", "2026-10-23T10:00:00+00:00"),
            )
        other = self.repository.create_integration(
            user_id="user-2", source_url=self.integration["source_url"], group_number=2,
            calendar_id="calendar-2", calendar_name="Other plan",
        )
        calls = []
        parsed = {"events": [], "changelog": []}

        async def exercise():
            with (
                patch("app.calendar.service.download_and_hash", new=AsyncMock(return_value=("new-hash", b"xlsx"))),
                patch("app.calendar.service.parse_downloaded_source", new=AsyncMock(return_value=parsed)),
                patch.object(
                    self.service, "reconcile",
                    side_effect=lambda integration, _schedule, **kwargs:
                        calls.append((integration["id"], kwargs["sync_trigger"], kwargs["emit_notifications"])),
                ),
            ):
                await self.service.refresh_source(
                    self.integration["source_url"], sync_trigger="integration_changed",
                    suppressed_integration_ids={self.integration["id"]},
                )

        asyncio.run(exercise())
        self.assertEqual(dict((item[0], item[1:]) for item in calls), {
            self.integration["id"]: ("integration_changed", False),
            other["id"]: ("source_changed", True),
        })


if __name__ == "__main__":
    unittest.main()
