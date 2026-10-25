from contextlib import closing
from dataclasses import replace
from hashlib import sha256
import os
import sqlite3
import tempfile
import unittest

from app.db.database import Database


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(os.path.join(self.tempdir.name, "test.sqlite3"))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_initializes_and_reruns_without_changing_schema(self):
        self.database.initialize()
        self.database.initialize()

        with self.database.read() as db:
            versions = [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        self.assertEqual(versions, list(range(1, len(Database._load_migrations()) + 1)))
        self.assertIn("users", tables)
        self.assertIn("timetable_links", tables)
        self.assertIn("push_subscriptions", tables)
        self.assertIn("user_feedback", tables)
        for removed_table in (
            "admin_test_push_audit", "integration_failure_streaks",
            "service_runtime_state", "app_preferences", "user_activity_events",
            "daily_operational_summaries",
        ):
            self.assertNotIn(removed_table, tables)
        with self.database.read() as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(push_subscriptions)")}
        self.assertIn("device_name", columns)

    def test_upgrades_a_database_created_with_the_previous_schema(self):
        first_migration = Database._load_migrations()[0]
        with closing(sqlite3.connect(self.database.path)) as db:
            db.executescript(first_migration.sql)

        self.database.initialize()

        with self.database.read() as db:
            versions = [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
            self.assertIsNotNone(
                db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='timetable_links'").fetchone()
            )
            self.assertEqual(versions, list(range(1, len(Database._load_migrations()) + 1)))
            self.assertIsNotNone(
                db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_feedback'").fetchone()
            )

    def test_refuses_to_change_an_already_applied_migration(self):
        self.database.initialize()
        migrations = Database._load_migrations()
        changed_sql = migrations[0].sql + "\n-- changed\n"
        changed_first = replace(
            migrations[0],
            sql=changed_sql,
            checksum=sha256(changed_sql.encode("utf-8")).hexdigest(),
        )

        with closing(self.database.connect()) as db:
            with self.assertRaisesRegex(RuntimeError, "has changed"):
                Database._apply_migrations(db, [changed_first, *migrations[1:]])

    def test_failed_migration_rolls_back_and_stops_the_migration_chain(self):
        migrations = [
            Database._load_migrations()[0],
            replace(
                Database._load_migrations()[1],
                sql="CREATE TABLE should_rollback(id INTEGER);\nTHIS IS NOT SQL;",
            ),
        ]
        with closing(self.database.connect()) as db:
            db.execute(
                "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, filename TEXT NOT NULL, "
                "checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            with self.assertRaisesRegex(RuntimeError, "migration 002"):
                Database._apply_migrations(db, migrations)

        with self.database.read() as db:
            versions = [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
            self.assertEqual(versions, [1])
            self.assertIsNone(
                db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='should_rollback'").fetchone()
            )

    def test_rejects_out_of_order_applied_history(self):
        migrations = Database._load_migrations()
        with self.database.connect() as db:
            db.execute(
                "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, filename TEXT NOT NULL, "
                "checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            db.execute(
                "INSERT INTO schema_migrations(version,filename,checksum,applied_at) VALUES(2,?,?,?)",
                (migrations[1].filename, migrations[1].checksum, "2026-10-24T00:00:00+00:00"),
            )
            with self.assertRaisesRegex(RuntimeError, "out of order"):
                Database._apply_migrations(db, migrations)


if __name__ == "__main__":
    unittest.main()
