import json
import os
import sqlite3
import tempfile
import unittest

from app.db.database import Database
from app.db.repository import Repository
from app.crypto.envelope import EnvelopeCrypto
from app.calendar.link_identity import timetable_link_key


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
        self.source_url = "https://www.umb.edu.pl/schedule.xlsx"
        self.link_key = timetable_link_key("https://www.umb.edu.pl/plany", "Schedule", self.source_url)
        with self.database.transaction(immediate=True) as db:
            db.execute(
                """INSERT INTO timetable_links(
                       url,name,programme,page_url,parser_key,link_key,discovered_at,last_seen_at,active
                   ) VALUES(?,?,?,?,?,?,?,?,1)""",
                (self.source_url, "Schedule", "Programme", "https://www.umb.edu.pl/plany", "G0",
                 self.link_key, "2026-10-23T10:00:00+00:00", "2026-10-23T10:00:00+00:00"),
            )
        self.integration = self.repository.create_integration(
            user_id="user-1",
            timetable_link_key=self.link_key,
            group_number=1,
            calendar_id="calendar-1",
            calendar_name="KiedyMam — grupa 1",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_one_integration_per_google_account_is_enforced_by_sqlite(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.create_integration(
                user_id="user-1",
                timetable_link_key=self.link_key,
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

    def test_workbook_inspection_limit_counts_distinct_urls_per_user(self):
        urls = [f"https://www.umb.edu.pl/plan-{index}.xlsx" for index in range(6)]
        for url in urls[:5]:
            accepted, retry_at = self.repository.reserve_workbook_inspection("user-1", url)
            self.assertTrue(accepted)
            self.assertIsNone(retry_at)

        with self.database.read() as db:
            attempts_before = json.loads(db.execute(
                "SELECT inspection_attempts_json FROM user_limits WHERE user_id=?", ("user-1",)
            ).fetchone()[0])
        self.assertEqual(len(attempts_before), 5)

        for _ in range(3):
            accepted, retry_at = self.repository.reserve_workbook_inspection("user-1", urls[0])
            self.assertTrue(accepted)
            self.assertIsNone(retry_at)

        accepted, retry_at = self.repository.reserve_workbook_inspection("user-1", urls[5])
        self.assertFalse(accepted)
        self.assertIsNotNone(retry_at)

        with self.database.read() as db:
            attempts_after = json.loads(db.execute(
                "SELECT inspection_attempts_json FROM user_limits WHERE user_id=?", ("user-1",)
            ).fetchone()[0])
        self.assertEqual(len(attempts_after), 5)
        self.assertEqual(
            {item["source_url"] for item in attempts_after},
            set(urls[:5]),
        )

    def test_workbook_inspection_limit_is_scoped_to_each_user(self):
        with self.database.transaction(immediate=True) as db:
            db.execute(
                "INSERT INTO users(id,google_sub,email,display_name,created_at) VALUES(?,?,?,?,?)",
                ("user-2", "google-2", "other@example.edu", "Other", "2026-10-23T10:00:00+00:00"),
            )

        for index in range(5):
            self.assertTrue(self.repository.reserve_workbook_inspection(
                "user-1", f"https://www.umb.edu.pl/plan-{index}.xlsx"
            )[0])
        self.assertTrue(self.repository.reserve_workbook_inspection(
            "user-2", "https://www.umb.edu.pl/plan-6.xlsx"
        )[0])

    def test_group_change_invalidates_the_reconciliation_hash(self):
        self.repository.mark_reconciled(self.integration["id"], "known-hash")

        updated = self.repository.update_group(self.integration["id"], "user-1", 3)

        self.assertEqual(updated["group_number"], 3)
        self.assertEqual(updated["calendar_name"], "KiedyMam — grupa 3")
        self.assertIsNone(updated["last_reconciled_sha256"])

    def test_calendar_name_can_be_changed_without_changing_the_group(self):
        updated = self.repository.update_group(
            self.integration["id"], "user-1", calendar_name="Plan"
        )
        self.assertEqual(updated["group_number"], 1)
        self.assertEqual(updated["calendar_name"], "Plan")

    def test_all_groups_calendar_name_has_a_default(self):
        self.assertEqual(
            self.repository.default_calendar_name(0), "KiedyMam — wszystkie zajęcia"
        )

    def test_sync_completion_notification_keeps_its_trigger_for_the_ui(self):
        self.repository.create_sync_completion_notification(self.integration, "manual")
        notification = self.repository.notifications("user-1")[0]
        self.assertEqual(notification["sync_trigger"], "manual")
        self.assertEqual(notification["action"], "sync_completed")

    def test_failed_integration_change_creates_a_failure_notice_not_an_event_diff(self):
        notice = self.repository.create_sync_failure_notification(
            self.integration, "integration_changed"
        )
        stored = self.repository.notifications("user-1")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["id"], notice["id"])
        self.assertEqual(stored[0]["action"], "sync_failed")
        self.assertEqual(stored[0]["sync_trigger"], "integration_changed")

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

    def test_successful_unchanged_download_can_extend_cache_age_without_replacing_parsed_data(self):
        parsed = {"events": [], "changelog": [], "_parser_version": 1}
        source_url = self.integration["source_url"]
        self.repository.upsert_source_cache(source_url, "same-hash", parsed)
        with self.database.transaction(immediate=True) as db:
            db.execute(
                "UPDATE source_cache SET fetched_at='2020-01-01T00:00:00+00:00' WHERE source_url=?",
                (source_url,),
            )

        self.assertTrue(self.repository.touch_source_cache(source_url))

        cache = self.repository.get_source_cache(source_url)
        self.assertEqual(cache["parsed"], parsed)
        self.assertNotEqual(cache["fetched_at"], "2020-01-01T00:00:00+00:00")

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
        self.assertEqual(notifications[0]["sync_trigger"], "scheduled")

    def test_integration_changes_can_suppress_event_notifications(self):
        event = {"date": "2026-10-26", "start": "08:00", "summary": "Anatomia"}
        self.repository.replace_calendar_events(
            self.integration, {}, [("added", event)], emit_notifications=False,
            sync_trigger="integration_changed",
        )
        self.assertEqual(self.repository.notifications("user-1"), [])

    def test_custom_calendar_name_is_mutable_and_preserved_when_group_changes(self):
        updated = self.repository.update_group(
            self.integration["id"], "user-1", 3, "Mój plan"
        )
        self.assertEqual(updated["group_number"], 3)
        self.assertEqual(updated["calendar_name"], "Mój plan")

    def test_deletion_notice_survives_removal_of_its_integration(self):
        self.repository.delete_integration(self.integration["id"], "user-1")
        notice = self.repository.create_integration_deletion_notification(self.integration, True)
        stored = self.repository.notifications("user-1")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["id"], notice["id"])
        self.assertIsNone(stored[0]["integration_id"])
        self.assertEqual(stored[0]["sync_trigger"], "integration_deleted")

    def test_push_subscriptions_are_encrypted_and_removable_per_device(self):
        crypto = EnvelopeCrypto(self.database, b"m" * 32)
        crypto.encrypt_for_user("user-1", {"access_token": "test"})
        subscription = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/token",
            "keys": {"p256dh": "p256dh-secret", "auth": "auth-secret"},
        }
        subscription_id = self.repository.save_push_subscription(
            "user-1", subscription, "Browser", crypto, "My laptop"
        )
        records = self.repository.push_subscriptions("user-1", crypto)
        self.assertEqual(records[0]["id"], subscription_id)
        self.assertEqual(records[0]["subscription"], subscription)
        self.assertEqual(records[0]["device_name"], "My laptop")
        self.assertTrue(self.repository.delete_push_subscription(subscription_id, "user-1"))
        self.assertEqual(self.repository.push_subscriptions("user-1", crypto), [])


if __name__ == "__main__":
    unittest.main()
