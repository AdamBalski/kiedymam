from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import logging
import time
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.auth.google import load_credentials, replace_credentials, SCOPES
from app.crypto.envelope import EnvelopeCrypto
from app.db.repository import Repository

LOG = logging.getLogger(__name__)
TIME_ZONE = "Europe/Warsaw"


def _credentials(user_id: str, repository: Repository, crypto: EnvelopeCrypto) -> Credentials:
    payload = load_credentials(crypto, user_id)
    # google-auth expects a naive UTC expiry and compares it with utcnow().
    expiry = datetime.fromtimestamp(int(payload.get("expires_at", 0)), timezone.utc).replace(tzinfo=None)
    credentials = Credentials(
        token=payload.get("access_token"),
        refresh_token=payload.get("refresh_token"),
        token_uri=payload["token_uri"],
        client_id=payload["client_id"],
        client_secret=payload["client_secret"],
        scopes=payload.get("scopes", list(SCOPES)),
        expiry=expiry,
    )
    if not credentials.valid or credentials.expired:
        if not credentials.refresh_token:
            raise RuntimeError("Google authorization expired; sign in again")
        credentials.refresh(Request())
        payload.update({
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "expires_at": int(credentials.expiry.timestamp()) if credentials.expiry else int(time.time()) + 3600,
        })
        replace_credentials(crypto, user_id, payload)
    return credentials


def calendar_api(user_id: str, repository: Repository, crypto: EnvelopeCrypto):
    credentials = _credentials(user_id, repository, crypto)
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def create_calendar(user_id: str, repository: Repository, crypto: EnvelopeCrypto, calendar_name: str) -> str:
    service = calendar_api(user_id, repository, crypto)
    result = service.calendars().insert(body={"summary": calendar_name, "timeZone": TIME_ZONE}).execute(num_retries=2)
    calendar_id = result.get("id")
    if not calendar_id:
        raise RuntimeError("Google did not return the new calendar identifier")
    return calendar_id


def delete_calendar(user_id: str, calendar_id: str, repository: Repository, crypto: EnvelopeCrypto) -> None:
    calendar_api(user_id, repository, crypto).calendars().delete(calendarId=calendar_id).execute(num_retries=2)


def rename_calendar(user_id: str, calendar_id: str, calendar_name: str, repository: Repository, crypto: EnvelopeCrypto) -> None:
    calendar_api(user_id, repository, crypto).calendars().patch(
        calendarId=calendar_id, body={"summary": calendar_name}
    ).execute(num_retries=2)


def event_identifier(integration_id: str, event_key: str) -> str:
    return sha256(f"{integration_id}:{event_key}".encode("utf-8")).hexdigest()


def _local_datetime(day: str, clock: str) -> str:
    local = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=ZoneInfo(TIME_ZONE))
    return local.isoformat()


def event_body(integration: dict, schedule_event: dict) -> dict:
    event_id = event_identifier(integration["id"], schedule_event["event_key"])
    description = "\n".join(
        (
            schedule_event.get("source_text", ""),
            f"Grupa: {integration['group_number']}",
            f"Plan: {integration['source_url']}",
        )
    )
    return {
        "id": event_id,
        "summary": schedule_event.get("summary") or "Zajęcia UMB",
        "description": description,
        "start": {"dateTime": _local_datetime(schedule_event["date"], schedule_event["start"]), "timeZone": TIME_ZONE},
        "end": {"dateTime": _local_datetime(schedule_event["date"], schedule_event["end"]), "timeZone": TIME_ZONE},
        "extendedProperties": {"private": {"umbSyncerIntegration": integration["id"], "umbSyncerKey": schedule_event["event_key"]}},
    }


def http_error_status(error: Exception) -> int | None:
    if isinstance(error, HttpError):
        return getattr(error.resp, "status", None)
    return None
