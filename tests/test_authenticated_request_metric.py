from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.api.auth import current_user


class AuthenticatedRequestMetricTests(unittest.TestCase):
    def test_authenticated_request_metric_has_email_as_its_only_dimension(self):
        calls = []
        metrics = SimpleNamespace(add_counter=lambda *args, **kwargs: calls.append((args, kwargs)))
        state = SimpleNamespace(settings=object(), database=object(), crypto=object(), metrics=metrics)
        request = SimpleNamespace(app=SimpleNamespace(state=state), cookies={"umb_session": "session"})
        user = {"id": "user-1", "email": " Student@Example.edu "}

        with patch("app.api.auth.google.get_session", return_value=user):
            self.assertEqual(current_user(request), user)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], ("http.server.authenticated_request.count",))
        self.assertEqual(calls[0][1]["attributes"], {"user.email": "student@example.edu"})


if __name__ == "__main__":
    unittest.main()
