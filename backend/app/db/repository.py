from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import uuid

from app.calendar.link_identity import timetable_link_key
from app.db.database import Database


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repository:
    def __init__(self, database: Database):
        self.database = database

    def timetable_configuration(self) -> dict:
        with self.database.read() as db:
            pages = db.execute(
                "SELECT label,page_url AS url FROM timetable_directory_pages ORDER BY position,page_url"
            ).fetchall()
            matchers = db.execute(
                "SELECT id,url_glob,parser_key,degree,study_year,study_mode,position FROM timetable_link_matchers ORDER BY position,id"
            ).fetchall()
        return {
            "pages": [dict(row) for row in pages],
            "matchers": [dict(row) for row in matchers],
        }

    def list_integrations(self, user_id: str) -> list[dict]:
        with self.database.read() as db:
            rows = db.execute(
                "SELECT * FROM integrations WHERE user_id=? ORDER BY created_at", (user_id,)
            ).fetchall()
            links_by_key = self._active_links_by_key(db)
        return self._with_current_source_urls(rows, links_by_key)

    @staticmethod
    def _active_links_by_key(db) -> dict[str, list[dict]]:
        rows = db.execute(
            "SELECT url,page_url,name,link_key FROM timetable_links WHERE active=1"
        ).fetchall()
        links_by_key: dict[str, list[dict]] = {}
        for row in rows:
            link = dict(row)
            key = link["link_key"] or timetable_link_key(
                link["page_url"], link["name"], link["url"]
            )
            links_by_key.setdefault(key, []).append(link)
        return links_by_key

    @staticmethod
    def _with_current_source_urls(rows, links_by_key: dict[str, list[dict]]) -> list[dict]:
        integrations = []
        for row in rows:
            integration = dict(row)
            matches = links_by_key.get(integration["timetable_link_key"], [])
            integration["source_url"] = matches[0]["url"] if len(matches) == 1 else None
            integrations.append(integration)
        return integrations

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
            links_by_key = self._active_links_by_key(db)
        return self._with_current_source_urls([row], links_by_key)[0] if row else None

    def all_source_urls(self) -> list[str]:
        with self.database.read() as db:
            integrations = db.execute(
                "SELECT timetable_link_key FROM integrations"
            ).fetchall()
            links_by_key = self._active_links_by_key(db)
        return sorted({
            links_by_key[row["timetable_link_key"]][0]["url"]
            for row in integrations
            if len(links_by_key.get(row["timetable_link_key"], [])) == 1
        })

    def integrations_for_source(self, source_url: str) -> list[dict]:
        with self.database.read() as db:
            link = db.execute(
                "SELECT page_url,name,link_key FROM timetable_links WHERE url=? AND active=1",
                (source_url,),
            ).fetchone()
            if not link:
                return []
            key = link["link_key"] or timetable_link_key(
                link["page_url"], link["name"], source_url
            )
            links_by_key = self._active_links_by_key(db)
            matches = links_by_key.get(key, [])
            if len(matches) != 1 or matches[0]["url"] != source_url:
                return []
            rows = db.execute(
                "SELECT * FROM integrations WHERE timetable_link_key=?", (key,)
            ).fetchall()
        return self._with_current_source_urls(rows, links_by_key)

    def create_integration(
        self, *, user_id: str, timetable_link_key: str, group_number: int,
        calendar_id: str, calendar_name: str,
    ) -> dict:
        integration_id = str(uuid.uuid4())
        now = utc_now()
        with self.database.transaction(immediate=True) as db:
            db.execute(
                """INSERT INTO integrations(
                       id,user_id,timetable_link_key,group_number,calendar_id,calendar_name,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (integration_id, user_id, timetable_link_key, group_number, calendar_id, calendar_name, now, now),
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

    def cache_missing_source(self, source_url: str, status: int) -> None:
        now = utc_now()
        missing = {"available": False, "http_status": status}
        with self.database.transaction(immediate=True) as db:
            db.execute(
                """INSERT INTO source_cache(source_url,fetched_at,content_sha256,parsed_json,availability,http_status)
                   VALUES(?,?,?,?, 'missing', ?)
                   ON CONFLICT(source_url) DO UPDATE SET fetched_at=excluded.fetched_at,
                     content_sha256=excluded.content_sha256,parsed_json=excluded.parsed_json,
                     availability='missing',http_status=excluded.http_status""",
                (source_url, now, f"http-{status}", json.dumps(missing, separators=(",", ":")), status),
            )

    def upsert_source_cache(self, source_url: str, digest: str, parsed: dict) -> bool:
        now = utc_now()
        with self.database.transaction(immediate=True) as db:
            prior = db.execute("SELECT content_sha256 FROM source_cache WHERE source_url=?", (source_url,)).fetchone()
            changed = not prior or prior["content_sha256"] != digest
            db.execute(
                """INSERT INTO source_cache(source_url,fetched_at,content_sha256,parsed_json,availability,http_status)
                   VALUES(?,?,?,?, 'available', NULL) ON CONFLICT(source_url) DO UPDATE SET fetched_at=excluded.fetched_at,
                   content_sha256=excluded.content_sha256,parsed_json=excluded.parsed_json,
                   availability='available',http_status=NULL""",
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
            "SELECT l.url,l.name,l.programme,l.page_url,l.parser_key,l.link_key,l.last_seen_at,"
            "m.degree,m.study_year,m.study_mode "
            "FROM timetable_links l JOIN timetable_link_matchers m ON m.id=l.link_key WHERE l.active=1"
        )
        args: list = []
        if query.strip():
            sql += " AND (l.name LIKE ? OR m.degree LIKE ? OR l.url LIKE ?)"
            term = f"%{query.strip()}%"
            args.extend((term, term, term))
        sql += " ORDER BY m.degree COLLATE NOCASE,m.study_year,m.study_mode LIMIT ?"
        args.append(max(1, min(limit, 2000)))
        with self.database.read() as db:
            rows = db.execute(sql, args).fetchall()
        links = [dict(row) for row in rows]
        for link in links:
            if not link["link_key"]:
                link["link_key"] = timetable_link_key(
                    link["page_url"], link["name"], link["url"]
                )
        return links

    def active_timetable_link_by_key(self, key: str) -> dict | None:
        with self.database.read() as db:
            rows = db.execute(
                """SELECT l.url,l.name,l.programme,l.page_url,l.parser_key,l.link_key,
                          m.degree,m.study_year,m.study_mode,m.availability
                   FROM timetable_links l JOIN timetable_link_matchers m ON m.id=l.link_key
                   WHERE l.active=1 AND l.link_key=?""",
                (key,),
            ).fetchall()
        return dict(rows[0]) if len(rows) == 1 else None

    def timetable_plan_by_key(self, key: str) -> dict | None:
        with self.database.read() as db:
            row = db.execute(
                """SELECT m.id AS link_key,m.degree,m.study_year,m.study_mode,m.availability,
                          l.url,l.name,l.programme,l.page_url,l.parser_key
                   FROM timetable_link_matchers m LEFT JOIN timetable_links l
                     ON l.link_key=m.id AND l.active=1 WHERE m.id=?""",
                (key,),
            ).fetchone()
        return dict(row) if row else None

    def timetable_plan_key_for_url(self, source_url: str) -> str | None:
        with self.database.read() as db:
            row = db.execute(
                "SELECT link_key FROM timetable_links WHERE url=? ORDER BY active DESC LIMIT 1",
                (source_url,),
            ).fetchone()
        return str(row["link_key"]) if row else None

    def set_timetable_plan_availability(
        self, link_key: str, available: bool, *, expected_url: str | None = None
    ) -> list[dict]:
        """Persist one availability transition and create at most one notice per owner."""
        target = "available" if available else "missing"
        now = utc_now()
        notices = []
        with self.database.transaction(immediate=True) as db:
            plan = db.execute(
                "SELECT availability FROM timetable_link_matchers WHERE id=?", (link_key,)
            ).fetchone()
            if not plan:
                return []
            if expected_url:
                current = db.execute(
                    "SELECT url FROM timetable_links WHERE link_key=? AND active=1", (link_key,)
                ).fetchone()
                if not current or current["url"] != expected_url:
                    return []
            if plan["availability"] == target:
                return []
            db.execute("UPDATE timetable_link_matchers SET availability=?,updated_at=? WHERE id=?", (target, now, link_key))
            integrations = db.execute(
                "SELECT id,user_id FROM integrations WHERE timetable_link_key=?", (link_key,)
            ).fetchall()
            for integration in integrations:
                action = "plan_available" if available else "plan_missing"
                message = (
                    "Plan zajęć jest ponownie dostępny na stronie UMB."
                    if available else "Plan zajęć nie jest obecnie dostępny na stronie UMB."
                )
                notice = {
                    "id": str(uuid.uuid4()), "user_id": integration["user_id"],
                    "integration_id": integration["id"], "action": action,
                    "event_summary": "Plan UMB", "event_start": now,
                    "message": message, "sync_trigger": "timetable_catalog",
                    "created_at": now,
                }
                db.execute(
                    """INSERT INTO notifications(id,user_id,integration_id,action,event_summary,event_start,message,sync_trigger,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    tuple(notice[key] for key in ("id","user_id","integration_id","action","event_summary","event_start","message","sync_trigger","created_at")),
                )
                notices.append(notice)
        return notices

    def timetable_parser_key(self, source_url: str) -> str | None:
        with self.database.read() as db:
            row = db.execute(
                "SELECT parser_key FROM timetable_links WHERE url=? AND active=1",
                (source_url,),
            ).fetchone()
        return row["parser_key"] if row else None

    def timetable_revisions_by_key(self, key: str) -> list[dict]:
        with self.database.read() as db:
            rows = db.execute(
                """SELECT url,name,programme,page_url,parser_key,link_key,last_modified
                   FROM timetable_links WHERE link_key=? AND listed=1 ORDER BY url""",
                (key,),
            ).fetchall()
        return [dict(row) for row in rows]

    def select_timetable_revision(self, key: str, url: str, last_modified: str | None) -> None:
        """Store a fresh HTTP validator and make the selected physical revision active."""
        with self.database.transaction(immediate=True) as db:
            db.execute(
                "UPDATE timetable_links SET last_modified=? WHERE link_key=? AND url=? AND listed=1",
                (last_modified, key, url),
            )
            db.execute("UPDATE timetable_links SET active=0 WHERE link_key=?", (key,))
            db.execute(
                "UPDATE timetable_links SET active=1 WHERE link_key=? AND url=? AND listed=1",
                (key, url),
            )

    def update_timetable_revision_dates(self, key: str, revisions: list[dict]) -> None:
        with self.database.transaction(immediate=True) as db:
            db.executemany(
                "UPDATE timetable_links SET last_modified=? WHERE link_key=? AND url=? AND listed=1",
                [(revision.get("last_modified"), key, revision["url"]) for revision in revisions],
            )

    def replace_timetable_links(
        self, links: list[dict], complete: bool, selected_urls: dict[str, str] | None = None
    ) -> None:
        if not complete:
            return
        now = utc_now()
        selected_urls = selected_urls or {}
        with self.database.transaction(immediate=True) as db:
            db.execute("UPDATE timetable_links SET active=0,listed=0")
            for link in links:
                active = int(selected_urls.get(str(link["link_key"])) == str(link["url"]))
                db.execute(
                    """INSERT INTO timetable_links(
                       url,name,programme,page_url,parser_key,link_key,last_modified,
                       discovered_at,last_seen_at,listed,active
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(url) DO UPDATE SET name=excluded.name,
                       programme=excluded.programme,page_url=excluded.page_url,
                       parser_key=excluded.parser_key,link_key=excluded.link_key,
                       last_modified=excluded.last_modified,
                       last_seen_at=excluded.last_seen_at,listed=1,active=excluded.active""",
                    (
                        link["url"], link["name"], link["programme"], link["page_url"],
                        link["parser_key"], link["link_key"], link.get("last_modified"),
                        now, now, 1, active,
                    ),
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
            integrations = db.execute(
                "SELECT * FROM integrations WHERE user_id=? ORDER BY calendar_name",
                (user_id,),
            ).fetchall()
            links_by_key = self._active_links_by_key(db)
        result = []
        for integration in self._with_current_source_urls(integrations, links_by_key):
            source_url = integration["source_url"]
            if not source_url:
                continue
            with self.database.read() as db:
                row = db.execute(
                    "SELECT parsed_json FROM source_cache WHERE source_url=?", (source_url,)
                ).fetchone()
            if not row:
                continue
            parsed = json.loads(row["parsed_json"])
            result.append({
                "calendar_name": integration["calendar_name"],
                "source_url": source_url,
                "entries": parsed.get("changelog", []),
            })
        return result
