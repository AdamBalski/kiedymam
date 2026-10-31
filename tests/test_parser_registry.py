import unittest
from unittest.mock import patch

from app.calendar.parser import ScheduleParseError
from app.calendar.parser_assignment import parser_key_for_url
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


class ParserRegistryTests(unittest.TestCase):
    def test_parser_key_dispatches_to_exactly_one_parser(self):
        g0 = StubParser("G0", error=ValueError("wrong workbook shape"))
        g1 = StubParser("G1", {"format": "G1"})
        with patch.dict(registry.PARSER_REGISTRY, {"G0": g0, "G1": g1}, clear=True):
            with self.assertRaisesRegex(ScheduleParseError, "Parser G0 rejected"):
                registry.parse_workbook_for_url(
                    b"workbook", "https://umb.edu.pl/schedule.xlsx", parser_key="G0"
                )

        self.assertEqual((g0.calls, g1.calls), (1, 0))

    def test_migration_matcher_assigns_a_single_parser_key(self):
        matchers = [{"url_glob": "*/[1-5]_fizjoterapia_*.xlsx", "parser_key": "G0"}]
        self.assertEqual(
            parser_key_for_url("https://www.umb.edu.pl/plany/3_fizjoterapia_zima_26.27.xlsx", matchers),
            "G0",
        )
        self.assertIsNone(parser_key_for_url("https://www.umb.edu.pl/plany/unknown.xlsx", matchers))

    def test_unknown_parser_key_fails_instead_of_trying_other_parsers(self):
        parser = StubParser("G0", {"format": "G0"})
        with patch.dict(registry.PARSER_REGISTRY, {"G0": parser}, clear=True):
            with self.assertRaisesRegex(ScheduleParseError, "No parser key is assigned"):
                registry.parse_workbook_for_url(
                    b"workbook", "https://umb.edu.pl/unknown.xlsx", parser_key="missing"
                )
        self.assertEqual(parser.calls, 0)

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
