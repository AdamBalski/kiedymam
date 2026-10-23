import os
import unittest
from unittest.mock import patch

from app.config import load_settings


CLIENT = '{"web":{"client_id":"client","client_secret":"secret","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token"}}'


class ConfigurationTests(unittest.TestCase):
    def test_local_origin_is_normalized(self):
        with patch.dict(os.environ, {"GOOGLE_OAUTH_CLIENT_JSON": CLIENT, "APP_ORIGIN": " http://LOCALHOST:8080/ ", "APP_BASE_PATH": ""}, clear=False):
            settings = load_settings()
        self.assertEqual(settings.app_origin, "http://localhost:8080")
        self.assertEqual(settings.google_redirect_uri, "http://localhost:8080/api/auth/google/callback")

    def test_public_base_path_is_canonicalized_and_used_for_oauth(self):
        with patch.dict(
            os.environ,
            {
                "GOOGLE_OAUTH_CLIENT_JSON": CLIENT,
                "APP_ORIGIN": "https://plans.example.edu",
                "APP_BASE_PATH": "/students/fizio/",
            },
            clear=False,
        ):
            settings = load_settings()
        self.assertEqual(settings.app_base_path, "/students/fizio")
        self.assertEqual(
            settings.google_redirect_uri,
            "https://plans.example.edu/students/fizio/api/auth/google/callback",
        )

    def test_public_origin_requires_https_and_origin_only(self):
        invalid = (
            "http://plans.example.edu",
            "https://plans.example.edu/application",
            "https://user@plans.example.edu",
            "https://plans.example.edu?next=/",
        )
        for origin in invalid:
            with self.subTest(origin=origin), patch.dict(
                os.environ, {"GOOGLE_OAUTH_CLIENT_JSON": CLIENT, "APP_ORIGIN": origin}, clear=False
            ):
                with self.assertRaises(RuntimeError):
                    load_settings()

    def test_base_path_rejects_ambiguous_or_unsafe_prefixes(self):
        for base_path in ("students/fizio", "/students/../fizio", "/students?next=/", "//students"):
            with self.subTest(base_path=base_path), patch.dict(
                os.environ,
                {
                    "GOOGLE_OAUTH_CLIENT_JSON": CLIENT,
                    "APP_ORIGIN": "https://plans.example.edu",
                    "APP_BASE_PATH": base_path,
                },
                clear=False,
            ):
                with self.assertRaises(RuntimeError):
                    load_settings()


if __name__ == "__main__":
    unittest.main()
