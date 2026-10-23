from __future__ import annotations

import logging

from googleapiclient.errors import HttpError

from app.calendar import gcal
from app.crypto.envelope import EnvelopeCrypto
from app.db.repository import Repository

LOG = logging.getLogger(__name__)


def _applies_to_group(event: dict, group_number: int) -> bool:
    if event.get("all_groups"):
        return True
    return group_number in {int(value) for value in event.get("group_numbers", []) if str(value).isdigit()}


def reconcile_integration(
    integration: dict,
    parsed_source: dict,
    repository: Repository,
    crypto: EnvelopeCrypto,
) -> dict:
    group_number = int(integration["group_number"])
    selected = {
        event["event_key"]: event
        for event in parsed_source.get("events", [])
        if _applies_to_group(event, group_number)
    }
    existing = repository.get_calendar_events(integration["id"])
    service = gcal.calendar_api(integration["user_id"], repository, crypto)
    current_calendar = service.calendars().get(calendarId=integration["calendar_id"]).execute(num_retries=2)
    if current_calendar.get("summary") != integration["calendar_name"]:
        service.calendars().patch(
            calendarId=integration["calendar_id"], body={"summary": integration["calendar_name"]}
        ).execute(num_retries=2)
    updated: dict[str, dict] = {}
    changes: list[tuple[str, dict]] = []

    for event_key, schedule_event in selected.items():
        body = gcal.event_body(integration, schedule_event)
        prior = existing.get(event_key)
        event_id = prior["google_event_id"] if prior else body["id"]
        body["id"] = event_id
        if prior and prior["event"].get("google") == body:
            updated[event_key] = prior
            continue
        if prior:
            service.events().update(
                calendarId=integration["calendar_id"], eventId=event_id, body=body, sendUpdates="none"
            ).execute(num_retries=2)
            action = "updated"
        else:
            try:
                service.events().insert(
                    calendarId=integration["calendar_id"], body=body, sendUpdates="none"
                ).execute(num_retries=2)
                action = "added"
            except HttpError as error:
                if gcal.http_error_status(error) != 409:
                    raise
                # The deterministic Google event ID makes retries safe after a partial run.
                service.events().update(
                    calendarId=integration["calendar_id"], eventId=event_id, body=body, sendUpdates="none"
                ).execute(num_retries=2)
                action = "added"
        updated[event_key] = {
            "google_event_id": event_id,
            "event": {"google": body, "schedule": schedule_event},
        }
        changes.append((action, schedule_event))

    for event_key, prior in existing.items():
        if event_key in selected:
            continue
        try:
            service.events().delete(
                calendarId=integration["calendar_id"], eventId=prior["google_event_id"], sendUpdates="none"
            ).execute(num_retries=2)
        except HttpError as error:
            if gcal.http_error_status(error) != 404:
                raise
        changes.append(("removed", prior["event"].get("schedule", {})))

    repository.replace_calendar_events(integration, updated, changes)
    LOG.info(
        "Calendar reconciliation complete integration=%s group=%s added_or_updated=%d removed=%d",
        integration["id"], group_number, sum(action != "removed" for action, _ in changes),
        sum(action == "removed" for action, _ in changes),
    )
    return {"added_or_updated": sum(action != "removed" for action, _ in changes), "removed": sum(action == "removed" for action, _ in changes)}
