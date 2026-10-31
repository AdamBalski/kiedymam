from __future__ import annotations

from typing import Callable, Protocol

from app.calendar.parser import ScheduleParseError
from app.calendar.parsers.g0 import G0Parser
from app.calendar.parsers.g1 import G1Parser
from app.calendar.parsers.m1 import M1Parser
from app.calendar.parsers.m2 import M2Parser


class WorkbookParser(Protocol):
    format_id: str

    def parse(self, contents: bytes) -> dict: ...


def _with_group_metadata(result: dict, format_id: str) -> dict:
    events = result.get("events", [])
    groups = {
        int(group)
        for event in events
        for group in event.get("group_numbers", [])
        if str(group).isdigit() and 1 <= int(group) <= 999
    }
    groups.update(
        int(group)
        for group in result.pop("_available_groups", [])
        if str(group).isdigit() and 1 <= int(group) <= 999
    )
    groups = sorted(groups)
    result["format_id"] = format_id
    result["available_groups"] = groups
    result["grouping_mode"] = (
        "groups" if groups else
        "all" if events and all(event.get("all_groups") for event in events) else
        "unknown"
    )
    return result


PARSER_REGISTRY: dict[str, WorkbookParser] = {
    "M1": M1Parser(),
    "M2": M2Parser(),
    "G0": G0Parser(),
    "G1": G1Parser(),
}

PARSER_KEYS = tuple(PARSER_REGISTRY)
# Bump this when parser behavior changes so persisted parsed JSON is rebuilt.
PARSER_CACHE_VERSION = 4


def parser_cache_version() -> int | str:
    return PARSER_CACHE_VERSION

def parse_workbook(contents: bytes, parser_key: str) -> dict:
    return parse_workbook_with_format(contents, parser_key)


def parse_workbook_with_format(contents: bytes, format_id: str) -> dict:
    parser_key = format_id.upper()
    parser = PARSER_REGISTRY.get(parser_key)
    if parser is None:
        raise ScheduleParseError(f"Unsupported timetable format: {format_id}")
    return _with_group_metadata(parser.parse(contents), parser_key)


def parse_workbook_for_url(
    contents: bytes, url: str | None,
    on_parser_result: Callable[[str | None], None] | None = None,
    *, parser_key: str | None = None,
) -> dict:
    selected = (parser_key or "").upper()
    parser = PARSER_REGISTRY.get(selected)
    if parser is None:
        raise ScheduleParseError(f"No parser key is assigned to this workbook (parser_key={selected or 'missing'})")
    try:
        result = parser.parse(contents)
    except Exception as exc:
        if on_parser_result:
            on_parser_result(None)
        raise ScheduleParseError(f"Parser {selected} rejected this workbook ({exc})") from exc
    if on_parser_result:
        on_parser_result(selected)
    return _with_group_metadata(result, selected)
