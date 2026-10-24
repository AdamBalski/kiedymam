import os
import tempfile
import unittest

from pydantic import ValidationError

from app.api.feedback import FeedbackRequest
from app.db.database import Database
from app.db.repository import Repository


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(os.path.join(self.tempdir.name, "feedback.sqlite3"))
        self.database.initialize()
        with self.database.transaction(immediate=True) as db:
            db.execute(
                "INSERT INTO users(id,google_sub,email,display_name,created_at) VALUES(?,?,?,?,?)",
                ("user-1", "google-1", "student@example.edu", "Student", "2026-10-24T10:00:00+00:00"),
            )
        self.repository = Repository(self.database)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_backend_accepts_3000_characters_and_rejects_3001(self):
        self.assertEqual(len(FeedbackRequest(body="x" * 3000).body), 3000)
        with self.assertRaises(ValidationError):
            FeedbackRequest(body="x" * 3001)

    def test_feedback_is_one_editable_record_and_can_be_removed(self):
        first = self.repository.save_feedback("user-1", "Pierwsza wiadomość")
        updated = self.repository.save_feedback("user-1", "Aktualna wiadomość")

        self.assertEqual(updated["body"], "Aktualna wiadomość")
        self.assertEqual(updated["created_at"], first["created_at"])
        with self.database.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM user_feedback").fetchone()[0], 1)

        self.assertTrue(self.repository.delete_feedback("user-1"))
        self.assertIsNone(self.repository.get_feedback("user-1"))
        self.assertFalse(self.repository.delete_feedback("user-1"))


if __name__ == "__main__":
    unittest.main()
