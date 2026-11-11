-- Synthetic v0.1.0 producer fixture; no operator data.
PRAGMA foreign_keys=ON;
BEGIN TRANSACTION;
PRAGMA defer_foreign_keys=ON;
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1)),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    csrf_token TEXT NOT NULL,
    expires_at TEXT NOT NULL
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
INSERT INTO "metadata" VALUES('app','notes');
INSERT INTO "metadata" VALUES('instance_name','retained');
INSERT INTO "notes" VALUES(1,'Current note','# Revision two 🌍

Keep every byte',0,2,2,'2026-11-09T00:48:10.486+00:00','2026-11-09T00:48:10.488+00:00');
INSERT INTO "notes" VALUES(2,'Archived note','Archived complete body',1,2,2,'2026-11-09T00:48:10.491+00:00','2026-11-09T00:48:10.494+00:00');
INSERT INTO "sessions" VALUES('eb34038a91fe36934e4ba67d662eae13ebf8116918bb0ae7b3c86b7aea284863',1,'E1ZOHy2Ke0UPYbT07LHBDGpSdC1nLQg2JiI5znb8lHw','2026-11-16T00:48:10.339+00:00');
INSERT INTO "sessions" VALUES('4456926d94e75cf3c8eaf4d5ec8d1747f5d35a3cd498c74ea0d17410d0cf89e5',3,'8XB_TNp9fjDsBiJM-4NsA3mWBx8R238a7Qa_72rljTg','2000-01-01T00:00:00+00:00');
INSERT INTO "users" VALUES(1,'admin','Administrator','scrypt$b99c905acf5f3382a83ddf8c93f1ab74$c8e18521af62136b263493b37db0f496e49c8cb3f5b495a4a6118a2e54fa071e7dfa6adae77d2597e45851e0aff3256a6f70a66a8cd3a27dc58f5fe79f677cf8','admin',1,'2026-11-09T00:48:10.303+00:00');
INSERT INTO "users" VALUES(2,'alice','Alice','scrypt$1f4ea4a8e2db2dc9d8f9e356b7c36dd2$3a32a8adabfbb06e703cc65da176cb7dbfd127f0b8848b914b68b61fa85392cf242ef534d8247a62b8075a21cd34aa06ea99a117a09127f0d0914f35ab162c01','member',0,'2026-11-09T00:48:10.375+00:00');
INSERT INTO "users" VALUES(3,'bob','Bob','scrypt$7ab6e7557135730b077f6faad3de18f4$4f1a66a65c62f05a4be6bb3d26922da1627930350665a20699cb6ee0735f905d96eec30de08cd8eb8cffe34f4ed4050ca850f2e7e3111482bebb0381cff70801','member',1,'2026-11-09T00:48:10.410+00:00');
CREATE INDEX sessions_user ON sessions(user_id);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('users',3);
INSERT INTO "sqlite_sequence" VALUES('notes',2);
PRAGMA user_version=1;
COMMIT;
