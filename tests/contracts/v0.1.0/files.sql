-- Synthetic v0.1.0 producer fixture; no operator data.
PRAGMA foreign_keys=ON;
BEGIN TRANSACTION;
PRAGMA defer_foreign_keys=ON;
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
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
INSERT INTO "files" VALUES(1,'Retained folder','folder',NULL,NULL,0,2,'2026-11-09T00:48:09.932+00:00','2026-11-09T00:48:09.932+00:00',NULL);
INSERT INTO "files" VALUES(2,'retained-☃.txt','file',1,'030834931cc64cb0a8a034c284a9b00d',38,2,'2026-11-09T00:48:09.937+00:00','2026-11-09T00:48:09.937+00:00',NULL);
INSERT INTO "files" VALUES(3,'Trashed folder','folder',NULL,NULL,0,2,'2026-11-09T00:48:09.940+00:00','2026-11-09T00:48:09.948+00:00','2026-11-09T00:48:09.948+00:00');
INSERT INTO "files" VALUES(4,'trashed.txt','file',3,'5cd2c9d75b694a08b309a91890618079',35,2,'2026-11-09T00:48:09.943+00:00','2026-11-09T00:48:09.945+00:00','2026-11-09T00:48:09.945+00:00');
INSERT INTO "metadata" VALUES('app','files');
INSERT INTO "metadata" VALUES('instance_name','retained');
INSERT INTO "sessions" VALUES('1a7ed573572e69686a28e8785a6e5c54165898d01ee379a4e4677b2962e83d6e',1,'rw7l5PM6L0zhNi2qXBaDKmxReLsXLzC1AJqRzotNzyA','2026-11-16T00:48:09.779+00:00');
INSERT INTO "sessions" VALUES('28a7a9086c843f4cc739e0354ed94267f528a5d2ea9a377a1ef787365af2d01a',3,'Rhq3pVodiufBXfJUXmoIkqZnMnOm6ZIE3y0Si-MLh1E','2000-01-01T00:00:00+00:00');
INSERT INTO "users" VALUES(1,'admin','Administrator','scrypt$031a59f74eba1b8cb78dc934feb37f4f$fc122d8719a5b0bf16410b14b7f90399b916236232755c6470d53bb3aeff7980f31b5abe1b370686ec10b8fcac2cfc249e9df63b7f8182e44dcf2dbcffd6a5c2','admin',1,'2026-11-09T00:48:09.739+00:00');
INSERT INTO "users" VALUES(2,'alice','Alice','scrypt$5cfddb71a54422d57cc6f038f2c62e41$9356a49e8283182d19ca834edb044433e117cbfdbf5087e7b7886a6088a1b7443f6094970d9d5f9780be115ab9ca741b6ce01c9097a45b0bd656fbd94748be84','member',0,'2026-11-09T00:48:09.817+00:00');
INSERT INTO "users" VALUES(3,'bob','Bob','scrypt$5998553af3e90cadfa7ccd00f6c1a742$b54e3d1a75474ced4b50826c1e31de11969bee43cf74486f5bd013fd80e457fc0faaae1e17a71dca731d955774a638ed5a2e6776c51ee11897f72889ef1ac137','member',1,'2026-11-09T00:48:09.853+00:00');
CREATE INDEX sessions_user ON sessions(user_id);
CREATE UNIQUE INDEX files_active_name ON files(COALESCE(parent_id,0),name) WHERE deleted_at IS NULL;
CREATE INDEX files_parent ON files(parent_id,id);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('users',3);
INSERT INTO "sqlite_sequence" VALUES('files',4);
PRAGMA user_version=1;
COMMIT;
