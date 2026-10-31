import json
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.auth import current_user
from app.crypto.envelope import EnvelopeCrypto
from app.main import app


GOOGLE_CLIENT = (
    '{"web":{"client_id":"inspection-client","client_secret":"inspection-secret",'
    '"auth_uri":"https://accounts.google.com/o/oauth2/auth",'
    '"token_uri":"https://oauth2.googleapis.com/token"}}'
)


class WorkbookInspectionApiTests(unittest.TestCase):
    def test_repeated_catalogue_workbook_does_not_consume_distinct_workbook_slots(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env = {
                "GOOGLE_OAUTH_CLIENT_JSON": GOOGLE_CLIENT,
                "APP_ORIGIN": "https://plans.example.edu",
                "APP_BASE_PATH": "",
                "OCI_SECRET_OCID": "ocid1.vaultsecret.oc1..inspection-test",
                "DATABASE_PATH": os.path.join(tempdir, "app.sqlite3"),
                "METRICS_DATABASE_PATH": os.path.join(tempdir, "metrics.sqlite3"),
            }
            user = {"id": "inspection-user", "email": "student@example.edu", "display_name": "Student"}
            with patch.dict(os.environ, env, clear=False), patch.object(
                EnvelopeCrypto, "load_master_key", return_value=b"k" * 32
            ), TestClient(app) as client:
                client.app.dependency_overrides[current_user] = lambda: user
                try:
                    with client.app.state.database.transaction(immediate=True) as db:
                        db.execute(
                            "INSERT INTO users(id,google_sub,email,display_name,created_at) VALUES(?,?,?,?,?)",
                            (user["id"], "inspection-google", user["email"], user["display_name"],
                             "2026-10-26T10:00:00+00:00"),
                        )
                        old_attempts = json.dumps([
                            {"timestamp": time.time() - 30 + index,
                             "source_url": "https://www.umb.edu.pl/plany/plan-0.xlsx"}
                            for index in range(1)
                        ])
                        db.execute(
                            "INSERT INTO user_limits(user_id,inspection_attempts_json,updated_at) VALUES(?,?,?)",
                            (user["id"], old_attempts, "2026-10-26T10:00:00+00:00"),
                        )
                        for index in range(6):
                            source_url = f"https://www.umb.edu.pl/plany/plan-{index}.xlsx"
                            page_url = "https://www.umb.edu.pl/plany"
                            plan_key = f"inspection-plan-{index}"
                            db.execute(
                                """INSERT INTO timetable_link_matchers(
                                       id,url_glob,parser_key,degree,study_year,study_mode,
                                       position,created_at,updated_at
                                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                                (plan_key, f"/plany/plan-{index}.xlsx", "G0",
                                 f"Programme {index}", 1, "stacjonarne", index,
                                 "2026-10-26T10:00:00+00:00", "2026-10-26T10:00:00+00:00"),
                            )
                            db.execute(
                                """INSERT INTO timetable_links(
                                       url,name,programme,page_url,parser_key,link_key,
                                       discovered_at,last_seen_at,active
                                   ) VALUES(?,?,?,?,?,?,?,?,1)""",
                                (source_url, f"Plan {index}", f"Programme {index}", page_url, "G0",
                                 plan_key,
                                 "2026-10-26T10:00:00+00:00", "2026-10-26T10:00:00+00:00"),
                            )
                    keys = [f"inspection-plan-{index}" for index in range(6)]
                    with patch.object(
                        client.app.state.calendar_service,
                        "get_source",
                        new=AsyncMock(return_value={
                            "format_id": "G0", "grouping_mode": "groups", "available_groups": [1, 2],
                        }),
                    ):
                        for _ in range(8):
                            response = client.get(
                                "/api/integrations/workbook-info", params={"timetable_link_key": keys[0]}
                            )
                            self.assertEqual(response.status_code, 200, response.text)
                            self.assertEqual(response.json()["source_url"],
                                             "https://www.umb.edu.pl/plany/plan-0.xlsx")

                        for index in range(1, 5):
                            response = client.get(
                                "/api/integrations/workbook-info",
                                params={"timetable_link_key": keys[index]},
                            )
                            self.assertEqual(response.status_code, 200, response.text)

                        blocked = client.get(
                            "/api/integrations/workbook-info",
                            params={"timetable_link_key": keys[5]},
                        )
                        self.assertEqual(blocked.status_code, 429, blocked.text)
                        self.assertIn("Retry-After", blocked.headers)

                        repeated_again = client.get(
                            "/api/integrations/workbook-info", params={"timetable_link_key": keys[0]}
                        )
                        self.assertEqual(repeated_again.status_code, 200, repeated_again.text)

                    with client.app.state.database.read() as db:
                        stored = json.loads(db.execute(
                            "SELECT inspection_attempts_json FROM user_limits WHERE user_id=?",
                            (user["id"],),
                        ).fetchone()[0])
                    self.assertEqual(len(stored), 5)
                    self.assertEqual(len({item["source_url"] for item in stored}), 5)
                finally:
                    client.app.dependency_overrides.pop(current_user, None)


if __name__ == "__main__":
    unittest.main()
