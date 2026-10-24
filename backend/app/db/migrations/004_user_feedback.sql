CREATE TABLE user_feedback (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    body TEXT NOT NULL CHECK(length(body) <= 3000),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
