import os
import sqlite3
import tempfile
import unittest

from app.db.database import Database
from app.db.repository import Repository


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(os.path.join(self.tempdir.name, "test.sqlite3"))
        self.database.initialize()
        with self.database.transaction(immediate=True) as db:
            db.execute(
                "INSERT INTO users(id,google_sub,email,display_name,created_at) VALUES(?,?,?,?,?)",
                ("user-1", "google-1", "student@example.edu", "Student", "2026-10-23T10:00:00+00:00"),
            )
        self.repository = Repository(self.database)
        self.integration = self.repository.create_integration(
            user_id="user-1",
            source_url="https://www.umb.edu.pl/schedule.xlsx",
            group_number=1,
            calendar_id="calendar-1",
            calendar_name="UMB Fizjoterapia — grupa 6 nr 1",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_one_integration_per_google_account_is_enforced_by_sqlite(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.create_integration(
                user_id="user-1",
                source_url="https://www.umb.edu.pl/another.xlsx",
                group_number=2,
                calendar_id="calendar-2",
                calendar_name="second calendar",
            )

    def test_sqlite_database_file_is_owner_only(self):
        self.assertEqual(os.stat(self.database.path).st_mode & 0o777, 0o600)

    def test_manual_sync_reservation_enforces_five_minute_cooldown(self):
        accepted, retry_at = self.repository.reserve_manual_sync(self.integration["id"], "user-1")
        self.assertTrue(accepted)
        self.assertIsNone(retry_at)

        accepted, retry_at = self.repository.reserve_manual_sync(self.integration["id"], "user-1")
        self.assertFalse(accepted)
        self.assertIsNotNone(retry_at)

    def test_group_change_invalidates_the_reconciliation_hash(self):
        self.repository.mark_reconciled(self.integration["id"], "known-hash")

        updated = self.repository.update_group(self.integration["id"], "user-1", 3)

        self.assertEqual(updated["group_number"], 3)
        self.assertEqual(updated["calendar_name"], "UMB Fizjoterapia — grupa 6 nr 3")
        self.assertIsNone(updated["last_reconciled_sha256"])

    def test_cache_upsert_reports_hash_changes_and_expired_entries_are_removed(self):
        parsed = {"events": [], "changelog": []}
        self.assertTrue(self.repository.upsert_source_cache(self.integration["source_url"], "hash-a", parsed))
        self.assertFalse(self.repository.upsert_source_cache(self.integration["source_url"], "hash-a", parsed))
        self.assertTrue(self.repository.upsert_source_cache(self.integration["source_url"], "hash-b", parsed))

        with self.database.transaction(immediate=True) as db:
            db.execute(
                "UPDATE source_cache SET fetched_at='2020-01-01T00:00:00+00:00' WHERE source_url=?",
                (self.integration["source_url"],),
            )
        self.assertEqual(self.repository.delete_expired_source_cache(12), 1)
        self.assertIsNone(self.repository.get_source_cache(self.integration["source_url"]))

    def test_reconciliation_persists_human_readable_notifications(self):
        event = {
            "date": "2026-10-26",
            "start": "08:00",
            "summary": "Anatomia",
            "source_text": "08:00-08:45 Anatomia",
        }
        record = {"google_event_id": "event-1", "event": {"schedule": event, "google": {"summary": "Anatomia"}}}
        self.repository.replace_calendar_events(
            self.integration,
            {"key-1": record},
            [("added", event)],
        )

        notifications = self.repository.notifications("user-1")

        self.assertEqual(len(notifications), 1)
        self.assertIn("Dodano: Anatomia", notifications[0]["message"])
        self.assertIn("2026-10-26 08:00", notifications[0]["message"])


if __name__ == "__main__":
    unittest.main()
