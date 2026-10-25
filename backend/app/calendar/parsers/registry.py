from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import re
from threading import Lock
from typing import Callable, Protocol
from urllib.parse import unquote, urlsplit

from app.calendar.parser import ScheduleParseError
from app.calendar.parsers.g0 import G0Parser
from app.calendar.parsers.g1 import G1Parser
from app.calendar.parsers.m1 import M1Parser
from app.calendar.parsers.m2 import M2Parser


class WorkbookParser(Protocol):
    format_id: str

    def parse(self, contents: bytes) -> dict: ...


@dataclass(frozen=True)
class ParserRegistration:
    parser: WorkbookParser
    url_patterns: tuple[re.Pattern[str], ...]


def _patterns(*expressions: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(expression, re.IGNORECASE) for expression in expressions)


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


SEMESTER = r"(?:zima|lato)"


PARSER_REGISTRY: dict[str, ParserRegistration] = {
    "M1": ParserRegistration(
        M1Parser(),
        _patterns(
            r"/(?:i|ii)_rok_lekarski_(?:stacjonarny|niestacjonarny)[^/]*\.xlsx$",
            r"/harmonogram_zajec_ii_roku_lekarskiego_.*\.xlsx$",
        ),
    ),
    "M2": ParserRegistration(M2Parser(), _patterns(r"/[3-6]_pl_(?:st|nst)_[^/]*\.xlsx$")),
    "G0": ParserRegistration(
        G0Parser(),
        _patterns(
            rf"/[1-5]_fizjoterapia_{SEMESTER}_[^/]*\.xlsx$",
            rf"/(?:[123]_[^/]*pz_{SEMESTER}|mgr_[12]_pz_{SEMESTER})[^/]*\.xlsx$",
            rf"/(?:[123]_pl_{SEMESTER}|mgr_[12]_pl_{SEMESTER})[^/]*\.xlsx$",
            rf"/(?:1|3)_ratownictwo_medyczne_{SEMESTER}[^/]*\.xlsx$",
            rf"/mgr_[12]_ratownictwo_medyczne_{SEMESTER}[^/]*\.xlsx$",
            r"/[12]_rok_elektroradiologia_ii_st\.[^/]*\.xlsx$",
        ),
    ),
    "G1": ParserRegistration(
        G1Parser(),
        _patterns(
            r"/(?:lekarsko_dentystyczny|techniki_dentystyczne|higiena_stomatologiczna|"
            r"biostatystyka_kliniczna|analityka_medyczna|farmacja|kosmetologia|"
            r"dietetyka(?:_\d{2,4})?|logopedia(?:_\d{2,4})?|"
            r"zdrowie_publiczne(?:_i_epidemiologia)?|psychologia)/.*\.xlsx$",
            r"/[^/]*(?:analityka_medyczna|farmacja|kosmetologia|psychologia|dietetyka|"
            r"zdrowie_publiczne(?:_i_epidemiologia)?)[^/]*\.xlsx$",
            rf"/nst_\d+_pl_{SEMESTER}_[^/]*\.xlsx$",
            rf"/2_ratownictwo_medyczne_{SEMESTER}[^/]*\.xlsx$",
            r"/[1-3]_rok_elektroradiologia_i_st\.[^/]*\.xlsx$",
        ),
    ),
}

FALLBACK_FORMATS = ("G0", "G1", "M1", "M2")
# Bump this when parser behavior changes so persisted parsed JSON is rebuilt.
PARSER_CACHE_VERSION = 2
MAX_REMEMBERED_LINKS = 1024
_remembered_parsers: OrderedDict[str, str] = OrderedDict()
_remembered_parsers_lock = Lock()

def parse_workbook(contents: bytes) -> dict:
    return parse_workbook_for_url(contents, None)


def parse_workbook_with_format(contents: bytes, format_id: str) -> dict:
    registration = PARSER_REGISTRY.get(format_id.upper())
    if registration is None:
        raise ScheduleParseError(f"Unsupported timetable format: {format_id}")
    return _with_group_metadata(registration.parser.parse(contents), format_id.upper())


def format_for_url(url: str) -> str | None:
    path = unquote(urlsplit(url).path).lower()
    for format_id, registration in PARSER_REGISTRY.items():
        if any(pattern.search(path) for pattern in registration.url_patterns):
            return format_id
    return None


def parse_workbook_for_url(
    contents: bytes, url: str | None,
    on_parser_result: Callable[[int, bool, str | None], None] | None = None,
) -> dict:
    preferred = format_for_url(url) if url else None
    link_key = urlsplit(url)._replace(query="", fragment="").geturl() if url else None
    remembered = None
    if link_key:
        with _remembered_parsers_lock:
            remembered = _remembered_parsers.get(link_key)
            if remembered:
                _remembered_parsers.move_to_end(link_key)
    formats = [remembered] if remembered else []
    ordered_candidates = ([preferred] if preferred else []) + list(FALLBACK_FORMATS)
    formats.extend(format_id for format_id in ordered_candidates if format_id not in formats)
    failures: list[str] = []
    for attempt, format_id in enumerate(formats, start=1):
        try:
            result = PARSER_REGISTRY[format_id].parser.parse(contents)
        except Exception as exc:
            # A parser can reject a workbook either explicitly or through a
            # low-level decoding/shape error. Treat either as a failed attempt
            # and give the next registered parser a chance.
            failures.append(f"{format_id}: {exc}")
            if link_key and format_id == remembered:
                with _remembered_parsers_lock:
                    _remembered_parsers.pop(link_key, None)
            continue
        if link_key:
            with _remembered_parsers_lock:
                _remembered_parsers[link_key] = format_id
                _remembered_parsers.move_to_end(link_key)
                while len(_remembered_parsers) > MAX_REMEMBERED_LINKS:
                    _remembered_parsers.popitem(last=False)
        if on_parser_result:
            fallback = (bool(preferred and format_id != preferred)
                        or (not preferred and attempt > 1))
            on_parser_result(attempt, fallback, format_id)
        return _with_group_metadata(result, format_id)
    if on_parser_result:
        on_parser_result(len(formats), len(formats) > 1, None)
    detail = "; ".join(failures)
    raise ScheduleParseError(f"No timetable parser accepted this workbook ({detail})")
