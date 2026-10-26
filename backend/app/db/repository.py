from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
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

    def get_user_by_email(self, email: str) -> dict | None:
        with self.database.read() as db:
            row = db.execute("SELECT id,email,display_name FROM users WHERE lower(email)=lower(?)", (email.strip(),)).fetchone()
        return dict(row) if row else None

    def active_integration_count(self) -> int:
        with self.database.read() as db:
            return int(db.execute("SELECT COUNT(*) FROM integrations").fetchone()[0])

    def get_feedback(self, user_id: str) -> dict | None:
        with self.database.read() as db:
            row = db.execute(
                "SELECT body,created_at,updated_at FROM user_feedback WHERE user_id=?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def save_feedback(self, user_id: str, body: str) -> dict:
        now = utc_now()
        with self.database.transaction(immediate=True) as db:
            db.execute(
                """INSERT INTO user_feedback(user_id,body,created_at,updated_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at""",
                (user_id, body, now, now),
            )
            row = db.execute(
                "SELECT body,created_at,updated_at FROM user_feedback WHERE user_id=?",
                (user_id,),
            ).fetchone()
        return dict(row)

    def delete_feedback(self, user_id: str) -> bool:
        with self.database.transaction(immediate=True) as db:
            cursor = db.execute("DELETE FROM user_feedback WHERE user_id=?", (user_id,))
        return cursor.rowcount > 0

    def reserve_workbook_inspection(
        self, user_id: str, source_url: str, *, limit: int = 5, interval_seconds: int = 3600
    ) -> tuple[bool, float | None]:
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - interval_seconds
        with self.database.transaction(immediate=True) as db:
            row = db.execute(
                "SELECT inspection_attempts_json FROM user_limits WHERE user_id=?", (user_id,)
            ).fetchone()
            try:
                attempts = json.loads(row["inspection_attempts_json"]) if row else []
            except (TypeError, json.JSONDecodeError):
                attempts = []
            recent_by_url = {}
            for item in attempts:
                if (not isinstance(item, dict)
                        or not isinstance(item.get("timestamp"), (int, float))
                        or item["timestamp"] <= cutoff
                        or not isinstance(item.get("source_url"), str)):
                    continue
                url = item["source_url"]
                recent_by_url[url] = max(float(item["timestamp"]), recent_by_url.get(url, 0.0))

            if source_url in recent_by_url:
                # Re-inspecting one workbook is allowed and does not consume another
                # distinct-workbook slot or extend its existing slot's expiry.
                pass
            elif len(recent_by_url) >= limit:
                retry_at = min(recent_by_url.values()) + interval_seconds
                encoded = json.dumps(
                    [{"timestamp": timestamp, "source_url": url}
                     for url, timestamp in recent_by_url.items()],
                    separators=(",", ":"),
                )
                db.execute(
                    "INSERT INTO user_limits(user_id,inspection_attempts_json,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET inspection_attempts_json=excluded.inspection_attempts_json,updated_at=excluded.updated_at",
                    (user_id, encoded, now.isoformat(timespec="seconds")),
                )
                return False, retry_at
            else:
                recent_by_url[source_url] = now.timestamp()

            attempts = [
                {"timestamp": timestamp, "source_url": url}
                for url, timestamp in recent_by_url.items()
            ]
            encoded = json.dumps(attempts, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) >= 16 * 1024:
                raise ValueError("Workbook inspection history exceeded its size limit")
            db.execute(
                "INSERT INTO user_limits(user_id,inspection_attempts_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET inspection_attempts_json=excluded.inspection_attempts_json,updated_at=excluded.updated_at",
                (user_id, encoded, now.isoformat(timespec="seconds")),
            )
        return True, None

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

    @staticmethod
    def default_calendar_name(group_number: int) -> str:
        return "KiedyMam — wszystkie zajęcia" if group_number == 0 else f"KiedyMam — grupa {group_number}"

    def update_group(
        self,
        integration_id: str,
        user_id: str,
        group_number: int | None = None,
        calendar_name: str | None = None,
    ) -> dict | None:
        with self.database.transaction(immediate=True) as db:
            prior = db.execute(
                "SELECT group_number,calendar_name,last_reconciled_sha256 FROM integrations WHERE id=? AND user_id=?",
                (integration_id, user_id),
            ).fetchone()
            if not prior:
                return None
            prior_group = int(prior["group_number"])
            new_group = int(group_number) if group_number is not None else prior_group
            if calendar_name is not None:
                new_name = calendar_name.strip()
            elif group_number is not None and prior["calendar_name"] == self.default_calendar_name(prior_group):
                new_name = self.default_calendar_name(new_group)
            else:
                new_name = prior["calendar_name"]
            new_reconciled_hash = None if new_group != prior_group else prior["last_reconciled_sha256"]
            cursor = db.execute(
                """UPDATE integrations SET group_number=?,calendar_name=?,last_reconciled_sha256=?,
                   updated_at=? WHERE id=? AND user_id=?""",
                (new_group, new_name, new_reconciled_hash, utc_now(), integration_id, user_id),
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

    def release_manual_sync(self, integration_id: str, user_id: str, reserved_at: str | None) -> bool:
        if not reserved_at:
            return False
        with self.database.transaction(immediate=True) as db:
            cursor = db.execute(
                "UPDATE integrations SET last_sync_at=NULL WHERE id=? AND user_id=? AND last_sync_at=?",
                (integration_id, user_id, reserved_at),
            )
        return cursor.rowcount > 0

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

    def touch_source_cache(self, source_url: str) -> bool:
        with self.database.transaction(immediate=True) as db:
            cursor = db.execute(
                "UPDATE source_cache SET fetched_at=? WHERE source_url=?",
                (utc_now(), source_url),
            )
        return cursor.rowcount > 0

    def delete_expired_source_cache(self, max_age_hours: int = 12) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
        with self.database.transaction(immediate=True) as db:
            cursor = db.execute(
                "DELETE FROM source_cache WHERE julianday(fetched_at) < julianday(?)",
                (cutoff,),
            )
        return cursor.rowcount

    def timetable_links(self, query: str = "", limit: int = 1000) -> list[dict]:
        sql = (
            "SELECT url,href_label,programme,page_url,format_id,last_seen_at "
            "FROM timetable_links WHERE active=1"
        )
        args: list = []
        if query.strip():
            sql += " AND (href_label LIKE ? OR programme LIKE ? OR url LIKE ?)"
            term = f"%{query.strip()}%"
            args.extend((term, term, term))
        sql += " ORDER BY programme COLLATE NOCASE,href_label COLLATE NOCASE LIMIT ?"
        args.append(max(1, min(limit, 2000)))
        with self.database.read() as db:
            rows = db.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def replace_timetable_links(self, links: list[dict], complete: bool) -> None:
        now = utc_now()
        with self.database.transaction(immediate=True) as db:
            if complete:
                db.execute("UPDATE timetable_links SET active=0")
            db.executemany(
                """INSERT INTO timetable_links(
                       url,href_label,programme,page_url,format_id,discovered_at,last_seen_at,active
                   ) VALUES(?,?,?,?,?,?,?,1)
                   ON CONFLICT(url) DO UPDATE SET href_label=excluded.href_label,
                       programme=excluded.programme,page_url=excluded.page_url,
                       format_id=excluded.format_id,last_seen_at=excluded.last_seen_at,active=1""",
                [
                    (
                        link["url"],
                        link["href_label"],
                        link["programme"],
                        link["page_url"],
                        link["format_id"],
                        now,
                        now,
                    )
                    for link in links
                ],
            )

    def get_calendar_events(self, integration_id: str) -> dict[str, dict]:
        with self.database.read() as db:
            rows = db.execute("SELECT event_key, google_event_id, event_json FROM calendar_events WHERE integration_id=?", (integration_id,)).fetchall()
        return {
            row["event_key"]: {"google_event_id": row["google_event_id"], "event": json.loads(row["event_json"])}
            for row in rows
        }

    def replace_calendar_events(
        self, integration: dict, desired: dict[str, dict], changes: list[tuple[str, dict]],
        *, emit_notifications: bool = True, sync_trigger: str = "scheduled",
    ) -> list[dict]:
        now = utc_now()
        created: list[dict] = []
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
            for action, event in (changes if emit_notifications else []):
                schedule = event.get("schedule", event)
                event_start = f"{schedule.get('date', '')}T{schedule.get('start', '')}"
                summary = schedule.get("summary") or schedule.get("source_text") or "Zajęcia"
                date_text = event_start.replace("T", " ")
                action_text = {"added": "Dodano", "updated": "Zmieniono", "removed": "Usunięto"}[action]
                trigger_text = {
                    "scheduled": "Automatyczna kontrola",
                    "manual": "Synchronizacja ręczna",
                    "source_changed": "Zmiana planu źródłowego",
                }.get(sync_trigger, sync_trigger)
                message = f"{trigger_text}: {action_text}: {summary} — {date_text}"
                item = {
                    "id": str(uuid.uuid4()), "user_id": integration["user_id"],
                    "integration_id": integration["id"], "action": action,
                    "event_summary": summary, "event_start": event_start,
                    "message": message, "sync_trigger": sync_trigger,
                    "created_at": now, "read_at": None,
                }
                db.execute(
                    """INSERT INTO notifications(id,user_id,integration_id,action,event_summary,event_start,message,sync_trigger,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (item["id"], item["user_id"], item["integration_id"], action, summary,
                     event_start, message, sync_trigger, now),
                )
                created.append(item)
            db.execute("UPDATE integrations SET last_sync_at=?, updated_at=? WHERE id=?", (now, now, integration["id"]))
        return created

    def create_sync_completion_notification(self, integration: dict, trigger: str) -> dict:
        labels = {
            "integration_created": "Integracja została utworzona",
            "integration_changed": "Ustawienia integracji zostały zmienione",
            "manual": "Ręczna synchronizacja została zakończona",
            "scheduled": "Automatyczna synchronizacja została zakończona",
            "source_changed": "Zakończono synchronizację po zmianie planu UMB",
        }
        item = {
            "id": str(uuid.uuid4()), "user_id": integration["user_id"],
            "integration_id": integration["id"], "action": "sync_completed",
            "event_summary": integration["calendar_name"], "event_start": "",
            "message": labels.get(trigger, "Synchronizacja została zakończona"),
            "sync_trigger": trigger, "created_at": utc_now(), "read_at": None,
        }
        with self.database.transaction(immediate=True) as db:
            db.execute(
                """INSERT INTO notifications(id,user_id,integration_id,action,event_summary,event_start,message,sync_trigger,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (item["id"], item["user_id"], item["integration_id"], item["action"],
                 item["event_summary"], item["event_start"], item["message"], trigger, item["created_at"]),
            )
        return item

    def create_sync_failure_notification(self, integration: dict, trigger: str) -> dict:
        labels = {
            "integration_created": "Nie udało się utworzyć kalendarza z tym planem.",
            "integration_changed": "Nie udało się zastosować zmienionych ustawień integracji.",
            "manual": "Nie udało się zakończyć ręcznej synchronizacji planu.",
        }
        item = {
            "id": str(uuid.uuid4()), "user_id": integration["user_id"],
            "integration_id": integration["id"], "action": "sync_failed",
            "event_summary": integration["calendar_name"], "event_start": "",
            "message": labels.get(trigger, "Synchronizacja planu nie powiodła się."),
            "sync_trigger": trigger, "created_at": utc_now(), "read_at": None,
        }
        with self.database.transaction(immediate=True) as db:
            db.execute(
                """INSERT INTO notifications(id,user_id,integration_id,action,event_summary,event_start,message,sync_trigger,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (item["id"], item["user_id"], item["integration_id"], item["action"],
                 item["event_summary"], item["event_start"], item["message"], trigger, item["created_at"]),
            )
        return item

    def create_integration_deletion_notification(self, integration: dict, calendar_deleted: bool) -> dict:
        message = (
            f"Integracja „{integration['calendar_name']}” i jej kalendarz zostały usunięte."
            if calendar_deleted else
            f"Integracja „{integration['calendar_name']}” została usunięta, ale kalendarz Google pozostał na koncie."
        )
        item = {
            "id": str(uuid.uuid4()), "user_id": integration["user_id"],
            "integration_id": None, "action": "integration_deleted",
            "event_summary": integration["calendar_name"], "event_start": "",
            "message": message, "sync_trigger": "integration_deleted",
            "created_at": utc_now(), "read_at": None,
        }
        with self.database.transaction(immediate=True) as db:
            db.execute(
                """INSERT INTO notifications(id,user_id,integration_id,action,event_summary,event_start,message,sync_trigger,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (item["id"], item["user_id"], None, item["action"], item["event_summary"],
                 "", message, item["sync_trigger"], item["created_at"]),
            )
        return item

    def save_push_subscription(
        self, user_id: str, subscription: dict, user_agent: str, crypto,
        device_name: str = "",
    ) -> str:
        endpoint = subscription["endpoint"]
        endpoint_hash = sha256(endpoint.encode("utf-8")).hexdigest()
        nonce, ciphertext = crypto.encrypt_payload(user_id, f"push:{endpoint_hash}", subscription)
        subscription_id = str(uuid.uuid4())
        with self.database.transaction(immediate=True) as db:
            db.execute(
                """INSERT INTO push_subscriptions(id,user_id,endpoint_hash,nonce,ciphertext,user_agent,device_name,created_at)
                   VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(endpoint_hash) DO UPDATE SET
                   user_id=excluded.user_id,nonce=excluded.nonce,ciphertext=excluded.ciphertext,
                   user_agent=excluded.user_agent,device_name=excluded.device_name,created_at=excluded.created_at""",
                (subscription_id, user_id, endpoint_hash, nonce, ciphertext,
                 user_agent[:300], device_name[:80], utc_now()),
            )
            row = db.execute("SELECT id FROM push_subscriptions WHERE endpoint_hash=?", (endpoint_hash,)).fetchone()
        return row["id"]

    def push_subscriptions(self, user_id: str, crypto) -> list[dict]:
        with self.database.read() as db:
            rows = db.execute(
                "SELECT * FROM push_subscriptions WHERE user_id=? ORDER BY created_at DESC", (user_id,)
            ).fetchall()
        return [{
            "id": row["id"], "user_id": user_id, "created_at": row["created_at"],
            "user_agent": row["user_agent"], "device_name": row["device_name"],
            "subscription": crypto.decrypt_payload(
                user_id, f"push:{row['endpoint_hash']}", bytes(row["nonce"]), bytes(row["ciphertext"])
            ),
        } for row in rows]

    def delete_push_subscription(self, subscription_id: str, user_id: str) -> bool:
        with self.database.transaction(immediate=True) as db:
            cursor = db.execute("DELETE FROM push_subscriptions WHERE id=? AND user_id=?", (subscription_id, user_id))
        return cursor.rowcount > 0

    def delete_push_subscription_by_hash(self, endpoint_hash: str) -> None:
        with self.database.transaction(immediate=True) as db:
            db.execute("DELETE FROM push_subscriptions WHERE endpoint_hash=?", (endpoint_hash,))

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
