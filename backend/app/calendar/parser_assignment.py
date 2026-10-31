from __future__ import annotations

from fnmatch import fnmatchcase
from urllib.parse import unquote, urlsplit


def matcher_for_url(url: str, matchers: list[dict]) -> dict | None:
    """Return the unique logical plan whose migration-configured glob matches a URL."""
    path = unquote(urlsplit(url).path).casefold()
    matches = [matcher for matcher in matchers if fnmatchcase(path, matcher["url_glob"].casefold())]
    if len(matches) > 1:
        raise ValueError(
            f"Timetable URL matched multiple logical plans: {', '.join(str(item['id']) for item in matches)}"
        )
    return matches[0] if matches else None


def parser_key_for_url(url: str, matchers: list[dict]) -> str | None:
    """Return the parser key only when a URL identifies exactly one configured plan."""
    matcher = matcher_for_url(url, matchers)
    return str(matcher["parser_key"]).upper() if matcher else None
