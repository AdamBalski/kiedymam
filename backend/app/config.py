import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class Settings:
    port: int
    database_path: str
    app_origin: str
    app_base_path: str
    google_client: dict
    oracle_secret_ocid: str
    timetable_directory_pages: tuple[tuple[str, str], ...]
    log_level: str
    web_push_secret_ocid: str
    web_push_subject: str

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.public_url('/api/auth/google/callback')}"

    def public_url(self, path: str) -> str:
        return f"{self.app_origin}{self.app_base_path}{path}"


def load_settings() -> Settings:
    raw_client = os.environ.get("GOOGLE_OAUTH_CLIENT_JSON", "")
    if not raw_client:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_JSON is required")
    try:
        client = json.loads(raw_client)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_JSON must be valid JSON") from exc
    if not isinstance(client, dict) or not isinstance(client.get("web"), dict):
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_JSON must contain the web client configuration")
    client_config = client["web"]
    for key in ("client_id", "client_secret", "auth_uri", "token_uri"):
        if not isinstance(client_config.get(key), str) or not client_config[key].strip():
            raise RuntimeError(f"GOOGLE_OAUTH_CLIENT_JSON web configuration is missing {key}")
    if not client_config["auth_uri"].startswith("https://") or not client_config["token_uri"].startswith("https://"):
        raise RuntimeError("Google OAuth authorization and token endpoints must use HTTPS")

    timetable_pages = _timetable_directory_pages_from_env()

    origin_value = os.environ.get("APP_ORIGIN", "http://localhost:8080").strip()
    try:
        parts = urlsplit(origin_value)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise RuntimeError("APP_ORIGIN must be a valid origin") from exc
    is_local_http = parts.scheme.lower() == "http" and host in {"localhost", "127.0.0.1"}
    if (
        not host
        or parts.scheme.lower() != "https" and not is_local_http
        or port == 0
        or parts.username
        or parts.password
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise RuntimeError("APP_ORIGIN must be an HTTPS origin (HTTP is allowed only for localhost development)")
    netloc = host.lower()
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if (port == 443 and parts.scheme.lower() == "https") or (port == 80 and is_local_http):
        port = None
    if port is not None:
        netloc = f"{netloc}:{port}"
    origin = urlunsplit((parts.scheme.lower(), netloc, "", "", ""))

    base_path_value = os.environ.get("APP_BASE_PATH", "").strip()
    if base_path_value in ("", "/"):
        base_path = ""
    else:
        if (
            not base_path_value.startswith("/")
            or base_path_value.startswith("//")
            or "?" in base_path_value
            or "#" in base_path_value
            or "\\" in base_path_value
            or not re.fullmatch(r"/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*/*", base_path_value)
        ):
            raise RuntimeError("APP_BASE_PATH must be a URL path prefix such as /students/fizio")
        segments = [segment for segment in base_path_value.split("/") if segment]
        if any(segment in {".", ".."} for segment in segments):
            raise RuntimeError("APP_BASE_PATH cannot contain dot segments")
        base_path = "/" + "/".join(segments)

    return Settings(
        port=_port_from_env(),
        database_path=os.environ.get("DATABASE_PATH", "/data/umb-syncer.sqlite3"),
        app_origin=origin,
        app_base_path=base_path,
        google_client=client,
        oracle_secret_ocid=os.environ.get("OCI_SECRET_OCID", "").strip(),
        timetable_directory_pages=timetable_pages,
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        web_push_secret_ocid=os.environ.get("OCI_WEB_PUSH_VAPID_SECRET_OCID", "").strip(),
        web_push_subject=os.environ.get(
            "WEB_PUSH_VAPID_SUBJECT", "https://adam-balski.duckdns.org/kiedymam"
        ).strip(),
    )


def _timetable_directory_pages_from_env() -> tuple[tuple[str, str], ...]:
    raw = os.environ.get("TIMETABLE_DIRECTORY_PAGES", "[]").strip() or "[]"
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("TIMETABLE_DIRECTORY_PAGES must be a JSON array") from exc
    if not isinstance(entries, list):
        raise RuntimeError("TIMETABLE_DIRECTORY_PAGES must be a JSON array")

    pages: list[tuple[str, str]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RuntimeError(f"TIMETABLE_DIRECTORY_PAGES item {index + 1} must be an object")
        label = entry.get("label")
        url = entry.get("url")
        if not isinstance(label, str) or not label.strip() or not isinstance(url, str):
            raise RuntimeError(f"TIMETABLE_DIRECTORY_PAGES item {index + 1} needs a label and URL")
        try:
            parts = urlsplit(url.strip())
            host = parts.hostname
            port = parts.port
        except ValueError as exc:
            raise RuntimeError(f"TIMETABLE_DIRECTORY_PAGES item {index + 1} has an invalid URL") from exc
        if (
            parts.scheme.lower() != "https"
            or not host
            or (host.lower() != "umb.edu.pl" and not host.lower().endswith(".umb.edu.pl"))
            or parts.username
            or parts.password
            or port not in (None, 443)
            or parts.query
            or parts.fragment
        ):
            raise RuntimeError(
                f"TIMETABLE_DIRECTORY_PAGES item {index + 1} must be an HTTPS UMB page URL"
            )
        pages.append((label.strip(), url.strip().rstrip("/")))
    return tuple(pages)


def _port_from_env() -> int:
    try:
        port = int(os.environ.get("PORT", "8080"))
    except ValueError as exc:
        raise RuntimeError("PORT must be an integer between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("PORT must be an integer between 1 and 65535")
    return port
