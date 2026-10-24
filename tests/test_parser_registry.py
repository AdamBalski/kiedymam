from __future__ import annotations

import re
import unittest
from unittest.mock import patch

from app.calendar.parser import ScheduleParseError
from app.calendar.parsers import registry
from app.calendar.reconcile import _applies_to_group


class StubParser:
    def __init__(self, format_id: str, result=None, error: Exception | None = None):
        self.format_id = format_id
        self.result = result
        self.error = error
        self.calls = 0

    def parse(self, contents: bytes) -> dict:
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def registrations(*parsers: StubParser) -> dict:
    return {
        parser.format_id: registry.ParserRegistration(parser, ())
        for parser in parsers
    }


class ParserRegistryTests(unittest.TestCase):
    def setUp(self):
        self.memory = patch.object(registry, "_remembered_parsers", registry.OrderedDict())
        self.memory.start()
        self.addCleanup(self.memory.stop)

    def test_unmatched_url_tries_registered_parsers_in_order_until_one_succeeds(self):
        g0 = StubParser("G0", error=ScheduleParseError("not G0"))
        g1 = StubParser("G1", {"format": "G1"})
        m1 = StubParser("M1", {"format": "M1"})
        registrations_for_test = registrations(g0, g1, m1)

        with (
            patch.dict(registry.PARSER_REGISTRY, registrations_for_test, clear=True),
            patch.object(registry, "FALLBACK_FORMATS", ("G0", "G1", "M1")),
        ):
            result = registry.parse_workbook_for_url(b"workbook", "https://umb.edu.pl/unknown.xlsx")

        self.assertEqual(result, {
            "format": "G1", "format_id": "G1", "available_groups": [], "grouping_mode": "unknown"
        })
        self.assertEqual([g0.calls, g1.calls, m1.calls], [1, 1, 0])

    def test_matching_url_parser_runs_first_then_falls_back_and_is_remembered(self):
        m1 = StubParser("M1", error=ValueError("unexpected workbook shape"))
        g0 = StubParser("G0", {"format": "G0"})
        registrations_for_test = registrations(m1, g0)
        registrations_for_test["M1"] = registry.ParserRegistration(
            m1, (re.compile(r"/medicine\.xlsx$", re.IGNORECASE),)
        )

        with (
            patch.dict(registry.PARSER_REGISTRY, registrations_for_test, clear=True),
            patch.object(registry, "FALLBACK_FORMATS", ("G0", "M1")),
        ):
            url = "https://umb.edu.pl/medicine.xlsx"
            first = registry.parse_workbook_for_url(b"workbook", url)
            # The cached parser is attempted first on subsequent refreshes.
            second = registry.parse_workbook_for_url(b"workbook", url)

        expected = {"format": "G0", "format_id": "G0", "available_groups": [], "grouping_mode": "unknown"}
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        self.assertEqual(m1.calls, 1)
        self.assertEqual(g0.calls, 2)

    def test_all_parser_failures_report_each_attempt(self):
        g0 = StubParser("G0", error=ScheduleParseError("bad grid"))
        g1 = StubParser("G1", error=ValueError("bad matrix"))
        with (
            patch.dict(registry.PARSER_REGISTRY, registrations(g0, g1), clear=True),
            patch.object(registry, "FALLBACK_FORMATS", ("G0", "G1")),
        ):
            with self.assertRaisesRegex(ScheduleParseError, "G0: bad grid; G1: bad matrix"):
                registry.parse_workbook_for_url(b"workbook", "https://umb.edu.pl/unknown.xlsx")

    def test_group_metadata_supports_degree_workbooks_with_more_than_36_groups(self):
        parsed = registry._with_group_metadata({
            "_available_groups": [90],
            "events": [
                {"group_numbers": [3, 64, 1200], "all_groups": False},
                {"group_numbers": [], "all_groups": True},
            ]
        }, "M2")
        self.assertEqual(parsed["available_groups"], [3, 64, 90])
        self.assertEqual(parsed["grouping_mode"], "groups")
        self.assertEqual(parsed["format_id"], "M2")

    def test_all_groups_selection_includes_only_explicitly_shared_events(self):
        self.assertTrue(_applies_to_group({"all_groups": True, "group_numbers": []}, 0))
        self.assertFalse(_applies_to_group({"all_groups": False, "group_numbers": [1]}, 0))


if __name__ == "__main__":
    unittest.main()
