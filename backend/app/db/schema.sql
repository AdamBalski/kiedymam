PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    google_sub TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS oauth_states (
    state_hash TEXT PRIMARY KEY,
    nonce TEXT NOT NULL,
    return_path TEXT NOT NULL DEFAULT '/',
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_credentials (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    nonce BLOB NOT NULL,
    ciphertext BLOB NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leaf_keys (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    nonce BLOB NOT NULL,
    wrapped_key BLOB NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integrations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    group_number INTEGER NOT NULL,
    calendar_id TEXT NOT NULL UNIQUE,
    calendar_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_sync_at TEXT,
    last_reconciled_sha256 TEXT
);
CREATE INDEX IF NOT EXISTS integrations_source_idx ON integrations(source_url);
CREATE INDEX IF NOT EXISTS integrations_user_idx ON integrations(user_id);

CREATE TABLE IF NOT EXISTS source_cache (
    source_url TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    parsed_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_events (
    integration_id TEXT NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
    event_key TEXT NOT NULL,
    google_event_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (integration_id, event_key),
    UNIQUE (integration_id, google_event_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    integration_id TEXT REFERENCES integrations(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    event_summary TEXT NOT NULL,
    event_start TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT
);
CREATE INDEX IF NOT EXISTS notifications_user_idx ON notifications(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS notifications_expiry_idx ON notifications(created_at);
