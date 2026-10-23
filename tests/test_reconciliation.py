import os
import tempfile
import unittest
from unittest.mock import patch

from app.calendar.reconcile import reconcile_integration
from app.db.database import Database
from app.db.repository import Repository


class FakeRequest:
    def __init__(self, action):
        self.action = action

    def execute(self, num_retries=None):
        return self.action()


class FakeCalendars:
    def __init__(self, service):
        self.service = service

    def get(self, calendarId):
        return FakeRequest(lambda: {"summary": self.service.calendar_summary})

    def patch(self, calendarId, body):
        return FakeRequest(lambda: self.service.set_calendar_summary(body["summary"]))


class FakeEvents:
    def __init__(self, service):
        self.service = service

    def insert(self, calendarId, body, sendUpdates):
        def insert():
            self.service.events_by_id[body["id"]] = body
            return body

        return FakeRequest(insert)

    def update(self, calendarId, eventId, body, sendUpdates):
        def update():
            self.service.events_by_id[eventId] = body
            return body

        return FakeRequest(update)

    def delete(self, calendarId, eventId, sendUpdates):
        def delete():
            self.service.events_by_id.pop(eventId, None)
            return {}

        return FakeRequest(delete)


class FakeCalendarApi:
    def __init__(self):
        self.calendar_summary = "old name"
        self.events_by_id = {}

    def calendars(self):
        return FakeCalendars(self)

    def events(self):
        return FakeEvents(self)

    def set_calendar_summary(self, summary):
        self.calendar_summary = summary
        return {"summary": summary}


class ReconciliationTests(unittest.TestCase):
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
        self.google = FakeCalendarApi()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_reconcile_filters_groups_and_applies_add_update_delete_idempotently(self):
        original = {
            "event_key": "event-a",
            "date": "2026-10-26",
            "start": "08:00",
            "end": "08:45",
            "summary": "Anatomia",
            "source_text": "Anatomia, S/Sala",
            "group_numbers": [1],
            "all_groups": False,
        }
        all_groups = {
            "event_key": "event-b",
            "date": "2026-10-27",
            "start": "10:00",
            "end": "10:45",
            "summary": "Fizjologia",
            "source_text": "Fizjologia, W/Sala",
            "group_numbers": [],
            "all_groups": True,
        }
        other_group = {**original, "event_key": "event-c", "group_numbers": [2]}

        with patch("app.calendar.reconcile.gcal.calendar_api", return_value=self.google):
            first = reconcile_integration(
                self.integration,
                {"events": [original, all_groups, other_group]},
                self.repository,
                crypto=None,
            )
            changed = {**original, "summary": "Anatomia poprawiona", "source_text": "Anatomia poprawiona, S/Sala"}
            second = reconcile_integration(
                self.integration,
                {"events": [changed, other_group]},
                self.repository,
                crypto=None,
            )
            repeated = reconcile_integration(
                self.integration,
                {"events": [changed, other_group]},
                self.repository,
                crypto=None,
            )

        self.assertEqual(first, {"added_or_updated": 2, "removed": 0})
        self.assertEqual(second, {"added_or_updated": 1, "removed": 1})
        self.assertEqual(repeated, {"added_or_updated": 0, "removed": 0})
        self.assertEqual(self.google.calendar_summary, self.integration["calendar_name"])
        self.assertEqual(len(self.google.events_by_id), 1)
        local = self.repository.get_calendar_events(self.integration["id"])
        self.assertEqual(len(local), 1)
        self.assertEqual(next(iter(local.values()))["event"]["schedule"]["summary"], "Anatomia poprawiona")
        self.assertEqual(len(self.repository.notifications("user-1")), 4)


if __name__ == "__main__":
    unittest.main()
