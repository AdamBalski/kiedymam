CREATE TABLE IF NOT EXISTS timetable_links (
    url TEXT PRIMARY KEY,
    href_label TEXT NOT NULL,
    programme TEXT NOT NULL,
    page_url TEXT NOT NULL,
    format_id TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS timetable_links_active_idx ON timetable_links(active, href_label);
