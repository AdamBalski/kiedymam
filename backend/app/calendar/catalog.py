from __future__ import annotations

from html.parser import HTMLParser
import logging
from pathlib import PurePosixPath
import time
from urllib.parse import urljoin, urlsplit

import httpx

from app.calendar.parsers.registry import format_for_url
from app.calendar.source import canonicalize_xlsx_url
from app.db.repository import Repository

LOG = logging.getLogger(__name__)
USER_AGENT = "KiedyMamTimetableCatalog/1.0"
REQUEST_TIMEOUT = httpx.Timeout(25, connect=5)
PAGE_DELAY_SECONDS = 10
MAX_REDIRECTS = 4


class WorkbookLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._active_href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "a":
            return
        self._active_href = dict(attrs).get("href")
        self._text = []

    def handle_data(self, data: str):
        if self._active_href:
            self._text.append(data)

    def handle_endtag(self, tag: str):
        if tag.lower() != "a" or not self._active_href:
            return
        label = " ".join(" ".join(self._text).split())
        self.links.append((self._active_href, label))
        self._active_href = None
        self._text = []


def _canonical_page_url(value: str) -> str:
    parts = urlsplit(value)
    host = parts.hostname
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("UMB timetable page has an invalid port") from exc
    if (
        parts.scheme.lower() != "https"
        or not host
        or (host.lower() != "umb.edu.pl" and not host.lower().endswith(".umb.edu.pl"))
        or parts.username
        or parts.password
        or port not in (None, 443)
    ):
        raise ValueError("Timetable pages and redirects must remain on HTTPS UMB origins")
    return parts._replace(scheme="https", netloc=host.lower(), fragment="").geturl()


def _get_page(client: httpx.Client, page_url: str) -> str:
    current = _canonical_page_url(page_url)
    for _ in range(MAX_REDIRECTS + 1):
        response = client.get(current, headers={"User-Agent": USER_AGENT}, follow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location")
            if not location:
                raise ValueError("UMB returned a redirect without a location")
            current = _canonical_page_url(urljoin(current, location))
            continue
        response.raise_for_status()
        return response.text
    raise ValueError("UMB timetable page redirected too many times")


class TimetableCatalog:
    def __init__(self, repository: Repository, pages: tuple[tuple[str, str], ...]):
        self.repository = repository
        self.pages = pages

    def refresh(self) -> None:
        if not self.pages:
            self.repository.replace_timetable_links([], complete=True)
            LOG.info("Timetable catalog refresh skipped; no directory pages are configured")
            return

        found: dict[str, dict] = {}
        complete = True
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            for index, (programme, page_url) in enumerate(self.pages):
                if index:
                    time.sleep(PAGE_DELAY_SECONDS)
                try:
                    page_html = _get_page(client, page_url)
                except Exception:
                    complete = False
                    LOG.exception("Could not read timetable directory page programme=%s url=%s", programme, page_url)
                    continue

                anchors = WorkbookLinks()
                anchors.feed(page_html)
                for href, href_label in anchors.links:
                    try:
                        workbook_url = canonicalize_xlsx_url(urljoin(page_url, href))
                    except ValueError:
                        continue
                    format_id = format_for_url(workbook_url)
                    if not format_id:
                        continue
                    filename = PurePosixPath(urlsplit(workbook_url).path).name
                    link_label = href_label or filename.rsplit(".", 1)[0].replace("_", " ").strip()
                    found.setdefault(
                        workbook_url,
                        {
                            "url": workbook_url,
                            "href_label": link_label or filename,
                            "programme": programme,
                            "page_url": page_url,
                            "format_id": format_id,
                        },
                    )

        self.repository.replace_timetable_links(list(found.values()), complete=complete)
        LOG.info(
            "Timetable catalog refresh complete pages=%d links=%d complete=%s",
            len(self.pages),
            len(found),
            complete,
        )
