from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
from hashlib import sha256
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, urljoin

import httpx

from app.calendar.parsers.registry import parse_workbook_for_url

LOG = logging.getLogger(__name__)
MAX_XLSX_BYTES = 5 * 1024 * 1024


def canonicalize_xlsx_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Enter a UMB XLSX link")
    raw = value.strip()
    parts = urlsplit(raw)
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise ValueError("The schedule link must use HTTPS")
    if parts.username or parts.password:
        raise ValueError("Credentials in schedule links are not allowed")
    host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    if host != "umb.edu.pl" and not host.endswith(".umb.edu.pl"):
        raise ValueError("The schedule link must be hosted on umb.edu.pl")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("The schedule link has an invalid port") from exc
    if port not in (None, 443):
        raise ValueError("The schedule link must use the standard HTTPS port")
    path = parts.path.rstrip("/")
    if not path.lower().endswith(".xlsx") or "\\" in path:
        raise ValueError("The link must point directly to an XLSX workbook")
    return urlunsplit(("https", host, path or "/", parts.query, ""))


async def _download(url: str) -> bytes:
    current = url
    async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=5), follow_redirects=False) as client:
        for _ in range(4):
            async with client.stream("GET", current, headers={"User-Agent": "KiedyMam-UMB-Calendar/0.1"}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("UMB returned an invalid redirect")
                    current = canonicalize_xlsx_url(urljoin(current, location))
                    continue
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_XLSX_BYTES:
                        LOG.warning(
                            "Rejected UMB workbook that exceeded the download limit "
                            "url=%s limit_bytes=%d received_at_least_bytes=%d",
                            current,
                            MAX_XLSX_BYTES,
                            len(content),
                        )
                        raise ValueError("The XLSX workbook is larger than the 5 MiB limit")
                data = bytes(content)
                if not data.startswith(b"PK\x03\x04"):
                    raise ValueError("UMB did not return an XLSX workbook")
                return data
    raise ValueError("UMB returned too many redirects")


async def download_and_hash(url: str) -> tuple[str, bytes]:
    canonical = canonicalize_xlsx_url(url)
    contents = await _download(canonical)
    digest = sha256(contents).hexdigest()
    return digest, contents


async def download_latest_revision(
    candidates: list[dict], on_probe=None, downloader=download_and_hash
) -> tuple[str, str | None, str, bytes]:
    """Probe all listed physical revisions and download the newest available one."""
    if not candidates:
        raise ValueError("No listed workbook revisions are available for this timetable plan")

    async def probe(candidate: dict) -> tuple[str, str | None, datetime | None, int | None]:
        original = canonicalize_xlsx_url(str(candidate["url"]))
        current = original
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=5), follow_redirects=False) as client:
            for _ in range(4):
                response = await client.head(
                    current,
                    headers={"User-Agent": "KiedyMam-UMB-Calendar/0.1"},
                )
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("UMB returned an invalid redirect")
                    current = canonicalize_xlsx_url(urljoin(current, location))
                    continue
                if response.status_code in (404, 410):
                    return original, None, None, response.status_code
                response.raise_for_status()
                raw = response.headers.get("last-modified")
                if not raw:
                    return original, None, None, None
                try:
                    parsed = parsedate_to_datetime(raw)
                except (TypeError, ValueError, OverflowError):
                    LOG.warning("UMB returned an invalid Last-Modified header url=%s", original)
                    return original, None, None, None
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                normalized = parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
                return original, normalized, parsed.astimezone(timezone.utc), None
        raise ValueError("UMB returned too many redirects")

    probed = await asyncio.gather(*(probe(candidate) for candidate in candidates))
    if on_probe:
        on_probe([{"url": item[0], "last_modified": item[1]} for item in probed])
    available = [item for item in probed if item[3] is None]
    if not available:
        status = 410 if any(item[3] == 410 for item in probed) else 404
        url = probed[0][0]
        request = httpx.Request("HEAD", url)
        response = httpx.Response(status, request=request)
        raise httpx.HTTPStatusError(
            f"All listed UMB workbook revisions were unavailable (HTTP {status})",
            request=request,
            response=response,
        )

    # Missing Last-Modified values sort below dated releases. The URL tie-break
    # keeps selection deterministic if UMB serves equal or absent timestamps.
    ordered = sorted(
        available,
        key=lambda item: (item[2] or datetime.min.replace(tzinfo=timezone.utc), item[0]),
        reverse=True,
    )
    last_missing_error = None
    for selected_url, last_modified, _, _ in ordered:
        try:
            digest, contents = await downloader(selected_url)
            return selected_url, last_modified, digest, contents
        except httpx.HTTPStatusError as error:
            if error.response.status_code not in (404, 410):
                raise
            last_missing_error = error
    if last_missing_error:
        raise last_missing_error
    raise ValueError("No available UMB workbook revision could be downloaded")


async def parse_downloaded_source(contents: bytes, url: str, on_parser_result=None, parser_key: str | None = None) -> dict:
    canonical = canonicalize_xlsx_url(url)
    return await asyncio.to_thread(
        parse_workbook_for_url, contents, canonical, on_parser_result, parser_key=parser_key
    )


async def fetch_and_parse(url: str, parser_key: str) -> tuple[str, dict]:
    canonical = canonicalize_xlsx_url(url)
    digest, contents = await download_and_hash(canonical)
    parsed = await parse_downloaded_source(contents, canonical, parser_key=parser_key)
    return digest, parsed
