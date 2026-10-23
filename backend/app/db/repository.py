from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid

from app.db.database import Database


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repository:
    def __init__(self, database: Database):
        self.database = database

    def list_integrations(self, user_id: str) -> list[dict]:
        with self.database.read() as db:
            rows = db.execute(
                "SELECT * FROM integrations WHERE user_id=? ORDER BY created_at", (user_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_integration(self, integration_id: str, user_id: str | None = None) -> dict | None:
        sql = "SELECT * FROM integrations WHERE id=?"
        args: tuple = (integration_id,)
        if user_id:
            sql += " AND user_id=?"
            args = (integration_id, user_id)
        with self.database.read() as db:
            row = db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all_source_urls(self) -> list[str]:
        with self.database.read() as db:
            rows = db.execute("SELECT DISTINCT source_url FROM integrations").fetchall()
        return [row["source_url"] for row in rows]

    def integrations_for_source(self, source_url: str) -> list[dict]:
        with self.database.read() as db:
            rows = db.execute("SELECT * FROM integrations WHERE source_url=?", (source_url,)).fetchall()
        return [dict(row) for row in rows]

    def create_integration(self, *, user_id: str, source_url: str, group_number: int, calendar_id: str, calendar_name: str) -> dict:
        integration_id = str(uuid.uuid4())
        now = utc_now()
        with self.database.transaction(immediate=True) as db:
            db.execute(
                """INSERT INTO integrations(id,user_id,source_url,group_number,calendar_id,calendar_name,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (integration_id, user_id, source_url, group_number, calendar_id, calendar_name, now, now),
            )
        return self.get_integration(integration_id, user_id)

    def update_group(self, integration_id: str, user_id: str, group_number: int) -> dict | None:
        calendar_name = f"UMB Fizjoterapia — grupa 6 nr {group_number}"
        with self.database.transaction(immediate=True) as db:
            cursor = db.execute(
                """UPDATE integrations SET group_number=?, calendar_name=?, last_reconciled_sha256=NULL,
                   updated_at=? WHERE id=? AND user_id=?""",
                (group_number, calendar_name, utc_now(), integration_id, user_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_integration(integration_id, user_id)

    def delete_integration(self, integration_id: str, user_id: str) -> bool:
        with self.database.transaction(immediate=True) as db:
            cursor = db.execute("DELETE FROM integrations WHERE id=? AND user_id=?", (integration_id, user_id))
        return cursor.rowcount > 0

    def reserve_manual_sync(self, integration_id: str, user_id: str, interval_seconds: int = 300) -> tuple[bool, str | None]:
        now = datetime.now(timezone.utc)
        with self.database.transaction(immediate=True) as db:
            row = db.execute(
                "SELECT last_sync_at FROM integrations WHERE id=? AND user_id=?",
                (integration_id, user_id),
            ).fetchone()
            if not row:
                return False, None
            last = datetime.fromisoformat(row["last_sync_at"]) if row["last_sync_at"] else None
            if last and (now - last).total_seconds() < interval_seconds:
                return False, (last.timestamp() + interval_seconds)
            db.execute(
                "UPDATE integrations SET last_sync_at=?, updated_at=? WHERE id=?",
                (now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"), integration_id),
            )
        return True, None

    def mark_sync_at(self, integration_id: str) -> None:
        now = utc_now()
        with self.database.transaction(immediate=True) as db:
            db.execute("UPDATE integrations SET last_sync_at=?, updated_at=? WHERE id=?", (now, now, integration_id))

    def mark_reconciled(self, integration_id: str, digest: str) -> None:
        with self.database.transaction(immediate=True) as db:
            db.execute(
                "UPDATE integrations SET last_reconciled_sha256=?, updated_at=? WHERE id=?",
                (digest, utc_now(), integration_id),
            )

    def get_source_cache(self, source_url: str) -> dict | None:
        with self.database.read() as db:
            row = db.execute("SELECT * FROM source_cache WHERE source_url=?", (source_url,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["parsed"] = json.loads(result.pop("parsed_json"))
        return result

    def upsert_source_cache(self, source_url: str, digest: str, parsed: dict) -> bool:
        now = utc_now()
        with self.database.transaction(immediate=True) as db:
            prior = db.execute("SELECT content_sha256 FROM source_cache WHERE source_url=?", (source_url,)).fetchone()
            changed = not prior or prior["content_sha256"] != digest
            db.execute(
                """INSERT INTO source_cache(source_url,fetched_at,content_sha256,parsed_json)
                   VALUES(?,?,?,?) ON CONFLICT(source_url) DO UPDATE SET fetched_at=excluded.fetched_at,
                   content_sha256=excluded.content_sha256,parsed_json=excluded.parsed_json""",
                (source_url, now, digest, json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))),
            )
        return changed

    def delete_expired_source_cache(self, max_age_hours: int = 12) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
        with self.database.transaction(immediate=True) as db:
            cursor = db.execute(
                "DELETE FROM source_cache WHERE julianday(fetched_at) < julianday(?)",
                (cutoff,),
            )
        return cursor.rowcount

    def get_calendar_events(self, integration_id: str) -> dict[str, dict]:
        with self.database.read() as db:
            rows = db.execute("SELECT event_key, google_event_id, event_json FROM calendar_events WHERE integration_id=?", (integration_id,)).fetchall()
        return {
            row["event_key"]: {"google_event_id": row["google_event_id"], "event": json.loads(row["event_json"])}
            for row in rows
        }

    def replace_calendar_events(
        self, integration: dict, desired: dict[str, dict], changes: list[tuple[str, dict]]
    ) -> None:
        now = utc_now()
        with self.database.transaction(immediate=True) as db:
            db.execute("DELETE FROM calendar_events WHERE integration_id=?", (integration["id"],))
            db.executemany(
                """INSERT INTO calendar_events(integration_id,event_key,google_event_id,event_json,updated_at)
                   VALUES(?,?,?,?,?)""",
                [
                    (integration["id"], event_key, record["google_event_id"], json.dumps(record["event"], ensure_ascii=False, sort_keys=True), now)
                    for event_key, record in desired.items()
                ],
            )
            for action, event in changes:
                schedule = event.get("schedule", event)
                event_start = f"{schedule.get('date', '')}T{schedule.get('start', '')}"
                summary = schedule.get("summary") or schedule.get("source_text") or "Zajęcia"
                date_text = event_start.replace("T", " ")
                action_text = {"added": "Dodano", "updated": "Zmieniono", "removed": "Usunięto"}[action]
                message = f"{action_text}: {summary} — {date_text}"
                db.execute(
                    """INSERT INTO notifications(id,user_id,integration_id,action,event_summary,event_start,message,created_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), integration["user_id"], integration["id"], action, summary, event_start, message, now),
                )
            db.execute("UPDATE integrations SET last_sync_at=?, updated_at=? WHERE id=?", (now, now, integration["id"]))

    def notifications(
        self, user_id: str, unread_only: bool = False, limit: int = 100, offset: int = 0
    ) -> list[dict]:
        sql = "SELECT * FROM notifications WHERE user_id=?"
        args: list = [user_id]
        if unread_only:
            sql += " AND read_at IS NULL"
        sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        args.extend((max(1, min(limit, 201)), max(0, offset)))
        with self.database.read() as db:
            rows = db.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def mark_notification_read(self, notification_id: str, user_id: str) -> bool:
        with self.database.transaction(immediate=True) as db:
            cursor = db.execute(
                "UPDATE notifications SET read_at=? WHERE id=? AND user_id=? AND read_at IS NULL",
                (utc_now(), notification_id, user_id),
            )
        return cursor.rowcount > 0

    def mark_all_notifications_read(self, user_id: str) -> int:
        with self.database.transaction(immediate=True) as db:
            cursor = db.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", (utc_now(), user_id))
        return cursor.rowcount

    def clean_notifications(self) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
        with self.database.transaction(immediate=True) as db:
            cursor = db.execute("DELETE FROM notifications WHERE julianday(created_at) < julianday(?)", (cutoff,))
        return cursor.rowcount

    def clean_auth_records(self) -> None:
        now = utc_now()
        with self.database.transaction(immediate=True) as db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            db.execute("DELETE FROM oauth_states WHERE expires_at < ?", (now,))

    def list_changelog(self, user_id: str) -> list[dict]:
        with self.database.read() as db:
            rows = db.execute(
                """SELECT DISTINCT c.source_url, c.parsed_json, i.calendar_name
                   FROM source_cache c JOIN integrations i ON i.source_url=c.source_url
                   WHERE i.user_id=? ORDER BY i.calendar_name""",
                (user_id,),
            ).fetchall()
        result = []
        for row in rows:
            parsed = json.loads(row["parsed_json"])
            result.append({"calendar_name": row["calendar_name"], "source_url": row["source_url"], "entries": parsed.get("changelog", [])})
        return result
