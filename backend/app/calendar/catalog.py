from __future__ import annotations

from html.parser import HTMLParser
import logging
from pathlib import PurePosixPath
from threading import Event, Lock
import time
from datetime import datetime
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx

from app.calendar.parser_assignment import matcher_for_url
from app.calendar.parsers.registry import PARSER_REGISTRY
from app.calendar.source import canonicalize_xlsx_url
from app.db.repository import Repository

LOG = logging.getLogger(__name__)
USER_AGENT = "KiedyMamTimetableCatalog/1.0"
REQUEST_TIMEOUT = httpx.Timeout(25, connect=5)
PAGE_DELAY_SECONDS = 10
MAX_REDIRECTS = 4


def _is_current_term(url: str) -> bool:
    """Keep a plan selector on the currently taught half-year when both are listed."""
    filename = PurePosixPath(urlsplit(url).path).name.casefold()
    winter = any(token in filename for token in ("zima", "zimowy", "zimowa"))
    summer = any(token in filename for token in ("lato", "letni", "letnia"))
    if winter == summer:
        return True
    month = datetime.now(ZoneInfo("Europe/Warsaw")).month
    current_winter = month in (10, 11, 12, 1, 2, 3)
    return winter == current_winter


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
    def __init__(self, repository: Repository, notification_callback=None, metrics=None):
        self.repository = repository
        self.notification_callback = notification_callback
        self.metrics = metrics
        self._lifecycle_lock = Lock()
        self._stopping = Event()

    def stop(self) -> None:
        """Prevent a background refresh from touching the DB after app shutdown."""
        with self._lifecycle_lock:
            self._stopping.set()

    def refresh(self) -> None:
        with self._lifecycle_lock:
            if self._stopping.is_set():
                return
            configuration = self.repository.timetable_configuration()
        unsupported_keys = {
            matcher["parser_key"].upper()
            for matcher in configuration["matchers"]
            if matcher["parser_key"].upper() not in PARSER_REGISTRY
        }
        if unsupported_keys:
            raise RuntimeError(
                "Timetable link matchers reference unsupported parser keys: "
                + ", ".join(sorted(unsupported_keys))
            )
        pages = [
            (page["label"], page["url"])
            for page in configuration["pages"]
        ]
        if not pages:
            with self._lifecycle_lock:
                if not self._stopping.is_set():
                    self.repository.replace_timetable_links([], complete=True)
            LOG.info("Timetable catalog refresh skipped; no directory pages are configured")
            return

        # Keep every physical release under its logical plan. The first listed
        # URL is a provisional active URL; source refreshes inspect Last-Modified
        # across the complete candidate set before downloading.
        found: dict[str, dict[str, dict]] = {}
        complete = True
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            for index, (programme, page_url) in enumerate(pages):
                if self._stopping.is_set():
                    return
                if index:
                    time.sleep(PAGE_DELAY_SECONDS)
                try:
                    page_html = _get_page(client, page_url)
                except Exception:
                    complete = False
                    LOG.exception("Could not read timetable directory page programme=%s url=%s", programme, page_url)
                    continue

                if self._stopping.is_set():
                    return

                anchors = WorkbookLinks()
                anchors.feed(page_html)
                for href, href_label in anchors.links:
                    try:
                        workbook_url = canonicalize_xlsx_url(urljoin(page_url, href))
                    except ValueError:
                        continue
                    if not _is_current_term(workbook_url):
                        continue
                    try:
                        matcher = matcher_for_url(workbook_url, configuration["matchers"])
                    except ValueError:
                        complete = False
                        LOG.exception("Workbook URL matched multiple timetable plans url=%s", workbook_url)
                        continue
                    if not matcher:
                        continue
                    parser_key = str(matcher["parser_key"]).upper()
                    if parser_key not in PARSER_REGISTRY:
                        complete = False
                        LOG.error("Timetable plan references unknown parser id=%s", matcher["id"])
                        continue
                    filename = PurePosixPath(urlsplit(workbook_url).path).name
                    link_label = href_label or filename.rsplit(".", 1)[0].replace("_", " ").strip()
                    found.setdefault(str(matcher["id"]), {}).setdefault(
                        workbook_url,
                        {
                            "url": workbook_url,
                            "name": link_label or filename,
                            "programme": programme,
                            "page_url": page_url,
                            "parser_key": parser_key,
                            "link_key": str(matcher["id"]),
                            "degree": str(matcher["degree"]),
                            "study_year": int(matcher["study_year"]),
                            "study_mode": str(matcher["study_mode"]),
                        },
                    )

        selected_links: dict[str, dict] = {}
        selected_urls: dict[str, str] = {}
        all_links: list[dict] = []
        for key, revisions in found.items():
            selected_url = next(iter(revisions))
            selected_links[key] = revisions[selected_url]
            selected_urls[key] = selected_url
            all_links.extend(revisions.values())
            if len(revisions) == 1:
                continue
            if self.metrics:
                self.metrics.add_counter(
                    "umb.timetable.plan.multiple_revisions.count",
                    attributes={"timetable.link.id": key, "copy.count": len(revisions)},
                )
            LOG.info(
                "Multiple current workbook revisions matched logical timetable plan "
                "id=%s copies=%d; source refresh will select by Last-Modified",
                key,
                len(revisions),
            )
        if self._stopping.is_set():
            return
        if complete:
            current_links = list(selected_links.values())
            notices_to_publish = []
            with self._lifecycle_lock:
                if self._stopping.is_set():
                    return
                previously_active = {
                    str(link["link_key"]): str(link["url"])
                    for link in self.repository.timetable_links()
                }
                self.repository.replace_timetable_links(all_links, complete=True, selected_urls=selected_urls)
                current_by_key = {link["link_key"]: link for link in current_links}
                for matcher in configuration["matchers"]:
                    link = current_by_key.get(str(matcher["id"]))
                    # A listing is not proof that the XLSX itself can be fetched.
                    # A present link is recovered only after a successful workbook download.
                    notices = [] if link else self.repository.set_timetable_plan_availability(
                        str(matcher["id"]), False
                    )
                    if not link and str(matcher["id"]) in previously_active:
                        # A complete catalogue scan acts as a 404 for a workbook
                        # that was previously listed. Store that result with the
                        # same negative cache representation as a failed XLSX GET.
                        self.repository.cache_missing_source(
                            previously_active[str(matcher["id"])], 404
                        )
                    if not link and self.metrics:
                        self.metrics.add_counter("umb.timetable.plan.unavailable.count", attributes={
                            "parser.key": str(matcher["parser_key"]).upper(), "outcome": "catalog_missing",
                        })
                    notices_to_publish.extend(notices)
            if notices_to_publish and self.notification_callback:
                self.notification_callback(notices_to_publish)
        LOG.info(
            "Timetable catalog refresh complete pages=%d links=%d complete=%s",
            len(pages),
            sum(len(links) for links in found.values()),
            complete,
        )
