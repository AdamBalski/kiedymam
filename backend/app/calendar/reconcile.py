from __future__ import annotations

import logging

from googleapiclient.errors import HttpError

from app.calendar import gcal
from app.crypto.envelope import EnvelopeCrypto
from app.db.repository import Repository

LOG = logging.getLogger(__name__)


def _applies_to_group(event: dict, group_number: int) -> bool:
    if group_number == 0:
        return bool(event.get("all_groups"))
    if event.get("all_groups"):
        return True
    return group_number in {int(value) for value in event.get("group_numbers", []) if str(value).isdigit()}


def reconcile_integration(
    integration: dict,
    parsed_source: dict,
    repository: Repository,
    crypto: EnvelopeCrypto,
    *,
    emit_notifications: bool = True,
    sync_trigger: str = "scheduled",
    metrics=None,
) -> dict:
    group_number = int(integration["group_number"])
    selected = {
        event["event_key"]: event
        for event in parsed_source.get("events", [])
        if _applies_to_group(event, group_number)
    }
    existing = repository.get_calendar_events(integration["id"])
    service = gcal.calendar_api(integration["user_id"], repository, crypto, metrics)
    current_calendar = gcal.execute(
        service.calendars().get(calendarId=integration["calendar_id"]),
        operation="google.calendar.calendars.get", metrics=metrics, num_retries=2,
    )
    if current_calendar.get("summary") != integration["calendar_name"]:
        gcal.execute(service.calendars().patch(
            calendarId=integration["calendar_id"], body={"summary": integration["calendar_name"]}
        ), operation="google.calendar.calendars.patch", metrics=metrics, num_retries=2)
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
            gcal.execute(service.events().update(
                calendarId=integration["calendar_id"], eventId=event_id, body=body, sendUpdates="none"
            ), operation="google.calendar.events.update", metrics=metrics, num_retries=2)
            action = "updated"
        else:
            try:
                gcal.execute(service.events().insert(
                    calendarId=integration["calendar_id"], body=body, sendUpdates="none"
                ), operation="google.calendar.events.insert", metrics=metrics, num_retries=2)
                action = "added"
            except HttpError as error:
                if gcal.http_error_status(error) != 409:
                    raise
                # The deterministic Google event ID makes retries safe after a partial run.
                gcal.execute(service.events().update(
                    calendarId=integration["calendar_id"], eventId=event_id, body=body, sendUpdates="none"
                ), operation="google.calendar.events.update", metrics=metrics, num_retries=2)
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
            gcal.execute(service.events().delete(
                calendarId=integration["calendar_id"], eventId=prior["google_event_id"], sendUpdates="none"
            ), operation="google.calendar.events.delete", metrics=metrics, num_retries=2)
        except HttpError as error:
            if gcal.http_error_status(error) != 404:
                raise
        changes.append(("removed", prior["event"].get("schedule", {})))

    change_counts = {"added": 0, "updated": 0, "removed": 0}
    for action, _event in changes:
        change_counts[action] += 1

    notifications = repository.replace_calendar_events(
        integration, updated, changes,
        emit_notifications=emit_notifications,
        sync_trigger=sync_trigger,
    )
    LOG.info(
        "Calendar reconciliation complete integration=%s group=%s added_or_updated=%d removed=%d",
        integration["id"], group_number, sum(action != "removed" for action, _ in changes),
        sum(action == "removed" for action, _ in changes),
    )
    return {
        **change_counts,
        "added_or_updated": sum(action != "removed" for action, _ in changes),
        "removed": sum(action == "removed" for action, _ in changes),
        "notifications": notifications,
    }
