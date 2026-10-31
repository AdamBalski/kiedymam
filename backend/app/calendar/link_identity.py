from __future__ import annotations

from hashlib import sha256
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit


def _normalized_name(name: str) -> str:
    value = " ".join((name or "").split()).casefold()
    value = re.sub(
        r"\b(?:z\s*(?:dn\.?|dnia)|v\.?)\s*\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?",
        " ", value,
    )
    value = re.sub(r"\b(?:19|20)?\d{2}[._/-](?:19|20)?\d{2}\b", " ", value)
    value = re.sub(r"\b\d{1,2}[./-](?:0?[1-9]|1[0-2])(?:[./-]\d{2,4})?\b", " ", value)
    value = re.sub(r"\b[0-9a-f]{8}\b", " ", value)
    return re.sub(r"[\s._/-]+", " ", value).strip()


def timetable_link_key(page_url: str, name: str, workbook_url: str | None = None) -> str:
    """Stable identity for a listed plan across URL, date, and position changes."""
    stable_name = _normalized_name(name)
    if not stable_name and workbook_url:
        filename = PurePosixPath(urlsplit(workbook_url).path).name
        stable_name = _normalized_name(filename.rsplit(".", 1)[0])
    identity = f"{page_url.rstrip('/')}\0{stable_name}"
    return sha256(identity.encode("utf-8")).hexdigest()
