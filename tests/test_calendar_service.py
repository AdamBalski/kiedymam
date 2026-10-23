import os
import tempfile
import unittest
from unittest.mock import patch

from google.auth.exceptions import RefreshError

from app.calendar.service import CalendarService
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


if __name__ == "__main__":
    unittest.main()
