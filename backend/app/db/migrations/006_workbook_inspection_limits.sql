CREATE TABLE user_limits (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    inspection_attempts_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);
