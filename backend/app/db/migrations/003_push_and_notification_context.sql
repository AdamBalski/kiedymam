ALTER TABLE notifications
    ADD COLUMN sync_trigger TEXT NOT NULL DEFAULT 'scheduled';

CREATE TABLE push_subscriptions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint_hash TEXT NOT NULL UNIQUE,
    nonce BLOB NOT NULL,
    ciphertext BLOB NOT NULL,
    user_agent TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX push_subscriptions_user_idx ON push_subscriptions(user_id, created_at DESC);
