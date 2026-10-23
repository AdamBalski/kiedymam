from __future__ import annotations

import asyncio
from hashlib import sha256
import logging
from urllib.parse import urlsplit, urlunsplit, urljoin

import httpx

from app.calendar.parser import parse_workbook

LOG = logging.getLogger(__name__)
MAX_XLSX_BYTES = 30 * 1024 * 1024


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
            async with client.stream("GET", current, headers={"User-Agent": "UMB-Fizjoterapia-Calendar/0.1"}) as response:
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
                        raise ValueError("The XLSX workbook is larger than the 30 MB limit")
                data = bytes(content)
                if not data.startswith(b"PK\x03\x04"):
                    raise ValueError("UMB did not return an XLSX workbook")
                return data
    raise ValueError("UMB returned too many redirects")


async def fetch_and_parse(url: str) -> tuple[str, dict]:
    canonical = canonicalize_xlsx_url(url)
    contents = await _download(canonical)
    digest = sha256(contents).hexdigest()
    parsed = await asyncio.to_thread(parse_workbook, contents)
    return digest, parsed
