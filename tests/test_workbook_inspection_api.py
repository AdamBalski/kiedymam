import json
import os
import tempfile
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
    def test_repeated_canonical_workbook_does_not_consume_distinct_workbook_slots(self):
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
                    with patch.object(
                        client.app.state.calendar_service,
                        "get_source",
                        new=AsyncMock(return_value={
                            "format_id": "G0", "grouping_mode": "groups", "available_groups": [1, 2],
                        }),
                    ):
                        repeated_url = " HTTPS://WWW.UMB.EDU.PL:443/plany/example.xlsx/ "
                        for _ in range(8):
                            response = client.get(
                                "/api/integrations/workbook-info", params={"source_url": repeated_url}
                            )
                            self.assertEqual(response.status_code, 200, response.text)
                            self.assertEqual(response.json()["source_url"],
                                             "https://www.umb.edu.pl/plany/example.xlsx")

                        for index in range(2, 6):
                            response = client.get(
                                "/api/integrations/workbook-info",
                                params={"source_url": f"https://www.umb.edu.pl/plany/plan-{index}.xlsx"},
                            )
                            self.assertEqual(response.status_code, 200, response.text)

                        blocked = client.get(
                            "/api/integrations/workbook-info",
                            params={"source_url": "https://www.umb.edu.pl/plany/sixth.xlsx"},
                        )
                        self.assertEqual(blocked.status_code, 429, blocked.text)
                        self.assertIn("Retry-After", blocked.headers)

                        repeated_again = client.get(
                            "/api/integrations/workbook-info", params={"source_url": repeated_url}
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
