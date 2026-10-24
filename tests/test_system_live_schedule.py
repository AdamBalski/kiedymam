from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import random
import unittest
from urllib.parse import urljoin, urlsplit

import httpx

from app.calendar.parsers.registry import parse_workbook_for_url
from app.calendar.source import _download


SCHEDULE_PAGE = "https://www.umb.edu.pl/wnoz/fizjoterapia/informacje/rozklady"


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


def _year_workbooks() -> list[str]:
    response = httpx.get(SCHEDULE_PAGE, timeout=30, follow_redirects=True)
    response.raise_for_status()
    links = WorkbookLinks()
    links.feed(response.text)
    candidates = set()
    for href in links.hrefs:
        url = urljoin(SCHEDULE_PAGE, href)
        parts = urlsplit(url)
        path = parts.path.lower()
        name = path.rsplit("/", 1)[-1]
        if (
            parts.hostname
            and (parts.hostname == "umb.edu.pl" or parts.hostname.endswith(".umb.edu.pl"))
            and "/fizjoterapia/zima/" in path
            and name[:1] in "12345"
            and "fizjoterapia_zima" in name
            and name.endswith(".xlsx")
        ):
            candidates.add(url)
    if not candidates:
        raise AssertionError("No year schedule XLSX links were found on the UMB schedule page")
    return sorted(candidates)


class LiveScheduleSystemTest(unittest.TestCase):
    def test_random_published_year_workbook_parses_as_a_plausible_plan(self):
        url = random.choice(_year_workbooks())
        with self.subTest(workbook=url):
            contents = asyncio.run(_download(url))
            self.assertTrue(contents.startswith(b"PK\x03\x04"), f"Not an XLSX workbook: {url}")
            result = parse_workbook_for_url(contents, url)
            events = result["events"]
            self.assertGreaterEqual(len(events), 100)
            self.assertLessEqual(len(events), 2000)
            self.assertTrue(result["changelog"], "Expected the workbook's published change log")

            for event in events:
                self.assertRegex(event["date"], r"^20\d\d-\d\d-\d\d$")
                self.assertRegex(event["start"], r"^\d\d:\d\d$")
                self.assertRegex(event["end"], r"^\d\d:\d\d$")
                self.assertTrue(event["summary"].strip())
                self.assertTrue(event["all_groups"] or event["group_numbers"])
                self.assertTrue(all(1 <= group <= 999 for group in event["group_numbers"]))


if __name__ == "__main__":
    unittest.main()
