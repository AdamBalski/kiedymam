import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from app.crypto.envelope import EnvelopeCrypto
from app.main import app


GOOGLE_CLIENT = (
    '{"web":{"client_id":"smoke-client","client_secret":"smoke-secret",'
    '"auth_uri":"https://accounts.google.com/o/oauth2/auth",'
    '"token_uri":"https://oauth2.googleapis.com/token"}}'
)


class AppLifecycleTests(unittest.TestCase):
    def test_startup_health_and_authentication_boundary(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env = {
                "GOOGLE_OAUTH_CLIENT_JSON": GOOGLE_CLIENT,
                "APP_ORIGIN": "https://plans.example.edu",
                "APP_BASE_PATH": "/students/fizio",
                "OCI_SECRET_OCID": "ocid1.vaultsecret.oc1..smoke",
                "DATABASE_PATH": os.path.join(tempdir, "smoke.sqlite3"),
            }
            with patch.dict(os.environ, env, clear=False), patch.object(
                EnvelopeCrypto, "load_master_key", return_value=b"k" * 32
            ), TestClient(app) as client:
                health = client.get("/api/health")
                self.assertEqual(health.status_code, 200)
                self.assertEqual(health.json(), {"status": "ok"})
                self.assertEqual(client.get("/api/integrations").status_code, 401)
                homepage = client.get("/")
                self.assertEqual(homepage.status_code, 200)
                self.assertIn('window.UMB_BASE_PATH="/students/fizio"', homepage.text)
                privacy = client.get("/privacy")
                terms = client.get("/terms")
                self.assertEqual(privacy.status_code, 200)
                self.assertIn("Wersja robocza", privacy.text)
                self.assertEqual(terms.status_code, 200)
                self.assertIn("Wersja robocza", terms.text)
                login = client.get("/api/auth/google", follow_redirects=False)
                self.assertEqual(login.status_code, 302)
                params = parse_qs(urlsplit(login.headers["location"]).query)
                self.assertEqual(params["client_id"], ["smoke-client"])
                self.assertEqual(
                    params["redirect_uri"],
                    ["https://plans.example.edu/students/fizio/api/auth/google/callback"],
                )
                self.assertIn("https://www.googleapis.com/auth/calendar.app.created", params["scope"][0])


if __name__ == "__main__":
    unittest.main()
