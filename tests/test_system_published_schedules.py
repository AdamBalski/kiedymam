from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
import os
from pathlib import PurePosixPath
import random
import re
import tempfile
import time
import unittest
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from app.calendar.parser import ScheduleParseError
from app.calendar.parser_assignment import parser_key_for_url
from app.calendar.parsers.registry import PARSER_KEYS, parse_workbook_for_url
from app.calendar.source import _download
from app.db.database import Database
from app.db.repository import Repository


SCHEDULE_PAGES = (
    ("Lekarski", "https://www.umb.edu.pl/pl/s,160/Harmonogramy_zajec"),
    ("Lekarsko-Dentystyczny", "https://www.umb.edu.pl/wl/kierunek-lekarsko-dentystyczny/informacje_dla_studentow/harmonogramy_zajec"),
    ("Techniki Dentystyczne", "https://www.umb.edu.pl/s,182/Harmonogramy_zajec"),
    ("Higiena Stomatologiczna", "https://www.umb.edu.pl/s,21977/harmonogramy_i_plany"),
    ("Biostatystyka Kliniczna I", "https://www.umb.edu.pl/s,25845/Harmonogramy__zajec"),
    ("Biostatystyka Kliniczna II", "https://www.umb.edu.pl/s,29333/Harmonogramy_zajec"),
    ("Bioinformatyka Medyczna", "https://www.umb.edu.pl/wl/bioinformatyka_medyczna/informacje_dla_studentow/harmonogramy_zajec"),
    ("Analityka Medyczna", "https://www.umb.edu.pl/wf/analityka-medyczna/informacje_dla_studentow/harmonogramy"),
    ("Farmacja", "https://www.umb.edu.pl/wf/farmacja/informacje_dla_studentow/harmonogramy"),
    ("Kosmetologia", "https://www.umb.edu.pl/wf/kosmetologia/informacje_dla_studentow/harmonogramy"),
    ("Dietetyka", "https://www.umb.edu.pl/wnoz/dietetyka/informacje/rozklady_zajec"),
    ("Elektroradiologia", "https://www.umb.edu.pl/s,8452/Rozklady_zajec"),
    ("Fizjoterapia", "https://www.umb.edu.pl/wnoz/fizjoterapia/informacje/rozklady"),
    ("Logopedia z Fonoaudiologią", "https://www.umb.edu.pl/wnoz/logopedia/rozklady"),
    ("Pielęgniarstwo", "https://www.umb.edu.pl/wnoz/pielegniarstwo/rozklady"),
    ("Położnictwo", "https://www.umb.edu.pl/wnoz/poloznictwo/informacje/rozklady"),
    ("Psychologia", "https://www.umb.edu.pl/s,32680/Harmonogramy_zajec"),
    ("Ratownictwo Medyczne", "https://www.umb.edu.pl/wnoz/ratownictwo/rozklady"),
    ("Zdrowie Publiczne i Epidemiologia", "https://www.umb.edu.pl/s,23654/Harmonogramy"),
    ("Zdrowie Publiczne II", "https://www.umb.edu.pl/wnoz/zdrowie_publiczne/informacje/rozklady"),
)

ROSTER_FILE = re.compile(
    r"(?:podzia[łl]|lista|listy|student|album|indeks|grupy|roster|00na0na1rel|0ajjj|0na0nastr|kopia)",
    re.IGNORECASE,
)
SYSTEM_TEST_USER_AGENT = "KiedymamPublishedScheduleSystemTest/1.0"
FAMILY_SAMPLE_SIZE = 2
TOTAL_SAMPLE_SIZE = 10


@dataclass(frozen=True)
class PublishedWorkbook:
    programme: str
    url: str
    format_id: str | None


class WorkbookLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(href)


def _schedule_links() -> list[PublishedWorkbook]:
    with tempfile.TemporaryDirectory() as tempdir:
        database = Database(os.path.join(tempdir, "schedule-test.sqlite3"))
        database.initialize()
        matchers = Repository(database).timetable_configuration()["matchers"]

    discovered: dict[str, PublishedWorkbook] = {}
    for programme, page_url in SCHEDULE_PAGES:
        response = httpx.get(
            page_url,
            headers={"User-Agent": SYSTEM_TEST_USER_AGENT},
            timeout=30,
            follow_redirects=True,
        )
        response.raise_for_status()
        links = WorkbookLinks()
        links.feed(response.text)
        for href in links.hrefs:
            url = urljoin(page_url, href)
            parts = urlsplit(url)
            path = unquote(parts.path)
            filename = PurePosixPath(path).name
            if (
                parts.scheme != "https"
                or not parts.hostname
                or (parts.hostname != "umb.edu.pl" and not parts.hostname.endswith(".umb.edu.pl"))
                or not path.lower().endswith(".xlsx")
                or ROSTER_FILE.search(filename)
            ):
                continue
            discovered.setdefault(
                url,
                PublishedWorkbook(
                    programme,
                    url,
                    parser_key_for_url(url, matchers),
                ),
            )
        time.sleep(float(os.getenv("UMB_SYSTEM_TEST_PAGE_INTERVAL_SECONDS", "1")))
    return list(discovered.values())


def _stratified_sample(candidates: list[PublishedWorkbook], rng: random.Random) -> list[PublishedWorkbook]:
    by_format = {format_id: [] for format_id in PARSER_KEYS}
    for item in candidates:
        if item.format_id in by_format:
            by_format[item.format_id].append(item)

    selected: list[PublishedWorkbook] = []
    for format_id in PARSER_KEYS:
        pool = by_format[format_id]
        rng.shuffle(pool)
        if len(pool) < FAMILY_SAMPLE_SIZE:
            raise AssertionError(f"Only {len(pool)} published plan links matched parser {format_id}")
        selected.extend(pool[:FAMILY_SAMPLE_SIZE])

    selected_urls = {item.url for item in selected}
    remaining = [
        item for item in candidates
        if item.url not in selected_urls and item.format_id in by_format
    ]
    rng.shuffle(remaining)
    selected.extend(remaining[: TOTAL_SAMPLE_SIZE - len(selected)])
    return selected


def _assert_plan_events(test: unittest.TestCase, result: dict, workbook_url: str) -> None:
    events = result.get("events")
    test.assertIsInstance(events, list, f"No event list returned for {workbook_url}")
    test.assertGreaterEqual(len(events), 10, f"Too few events parsed from {workbook_url}")
    test.assertLessEqual(len(events), 5000, f"Implausibly many events parsed from {workbook_url}")
    for event in events:
        location = f"{workbook_url} ({event.get('source_cell')})"
        test.assertRegex(event.get("date", ""), r"^20\d\d-\d\d-\d\d$", location)
        test.assertRegex(event.get("start", ""), r"^\d\d:\d\d$", location)
        test.assertRegex(event.get("end", ""), r"^\d\d:\d\d$", location)
        test.assertTrue(event.get("summary", "").strip(), location)
        test.assertIsNone(
            re.fullmatch(r"\d{1,2}[.:]\d{2}\s*[-–]\s*\d{1,2}[.:]\d{2}", event.get("summary", "")),
            location,
        )
        test.assertTrue(all(1 <= int(group) <= 999 for group in event.get("group_numbers", [])), location)


class PublishedScheduleSystemTests(unittest.TestCase):
    def test_random_ten_published_plans_parse_with_registered_adapters(self):
        candidates = _schedule_links()
        self.assertGreaterEqual(len(candidates), TOTAL_SAMPLE_SIZE)
        seed = int(os.getenv("UMB_SYSTEM_TEST_SEED", str(date.today().toordinal())))
        rng = random.Random(seed)
        sample = _stratified_sample(candidates, rng)
        selected_urls = {item.url for item in sample}
        remaining = [
            item for item in candidates
            if item.url not in selected_urls and item.format_id in PARSER_KEYS
        ]
        rng.shuffle(remaining)
        successful: list[PublishedWorkbook] = []
        successful_formats: dict[str, int] = {format_id: 0 for format_id in PARSER_KEYS}
        interval = float(os.getenv("UMB_SYSTEM_TEST_WORKBOOK_INTERVAL_SECONDS", "16"))
        last_request_at: float | None = None
        attempts = 0
        max_attempts = min(len(candidates), TOTAL_SAMPLE_SIZE + 10)

        pending = list(sample)
        while len(successful) < TOTAL_SAMPLE_SIZE and attempts < max_attempts:
            if pending:
                item = pending.pop(0)
            else:
                underrepresented = next(
                    (format_id for format_id in PARSER_KEYS if successful_formats[format_id] < FAMILY_SAMPLE_SIZE),
                    None,
                )
                backup_index = next(
                    (
                        index for index, candidate in enumerate(remaining)
                        if underrepresented is None or candidate.format_id == underrepresented
                    ),
                    None,
                )
                if backup_index is None:
                    break
                item = remaining.pop(backup_index)

            attempts += 1
            if last_request_at is not None:
                time.sleep(max(0, interval - (time.monotonic() - last_request_at)))
            last_request_at = time.monotonic()
            try:
                contents = asyncio.run(_download(item.url))
            except ValueError as exc:
                # Some UMB pages retain obsolete .xlsx links that return a small HTML page.
                if "did not return an XLSX workbook" in str(exc):
                    continue
                raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (404, 410):
                    continue
                raise
            except httpx.TimeoutException:
                # One overloaded endpoint should not stop the daily sample;
                # another link from the same parser family will be selected.
                continue

            self.assertTrue(contents.startswith(b"PK\x03\x04"), f"Not an XLSX workbook: {item.url}")
            try:
                result = parse_workbook_for_url(contents, item.url, parser_key=item.format_id)
            except ScheduleParseError as exc:
                self.fail(f"No registered parser accepted {item.url}: {exc}")
            _assert_plan_events(self, result, item.url)
            successful.append(item)
            if item.format_id in successful_formats:
                successful_formats[item.format_id] += 1

        self.assertEqual(
            len(successful),
            TOTAL_SAMPLE_SIZE,
            f"Only {len(successful)} valid plans parsed in {attempts} attempts; "
            f"selected {[(item.format_id, item.url) for item in sample]}",
        )
        self.assertTrue(all(successful_formats[format_id] >= FAMILY_SAMPLE_SIZE for format_id in PARSER_KEYS))


if __name__ == "__main__":
    unittest.main()
