CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL
);
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    csrf_token TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX sessions_user ON sessions(user_id);
PRAGMA user_version = 1;

CREATE TABLE files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('file','folder')),
    parent_id INTEGER REFERENCES files(id),
    blob_key TEXT UNIQUE,
    size INTEGER NOT NULL DEFAULT 0 CHECK (size >= 0),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    CHECK ((kind='folder' AND blob_key IS NULL AND size=0) OR (kind='file' AND blob_key IS NOT NULL))
);
CREATE UNIQUE INDEX files_active_name ON files(COALESCE(parent_id,0),name) WHERE deleted_at IS NULL;
CREATE INDEX files_parent ON files(parent_id,id);
