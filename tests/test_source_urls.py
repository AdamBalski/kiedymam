import unittest

from app.calendar.source import canonicalize_xlsx_url


class SourceUrlTests(unittest.TestCase):
    def test_canonicalizes_a_direct_umb_workbook_url(self):
        value = "  HTTPS://WWW.UMB.EDU.PL:443/plany/example.xlsx/?download=1#section  "
        self.assertEqual(
            canonicalize_xlsx_url(value),
            "https://www.umb.edu.pl/plany/example.xlsx?download=1",
        )

    def test_rejects_non_umb_and_non_https_sources(self):
        invalid = (
            "http://www.umb.edu.pl/plany/example.xlsx",
            "https://umb.edu.pl.example.com/plany/example.xlsx",
            "https://example.com/plany/example.xlsx",
            "https://www.umb.edu.pl/plany/example.xls",
            "https://user@www.umb.edu.pl/plany/example.xlsx",
        )
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError):
                canonicalize_xlsx_url(url)


if __name__ == "__main__":
    unittest.main()
