-- Synthetic v0.1.0 producer fixture; no operator data.
PRAGMA foreign_keys=ON;
BEGIN TRANSACTION;
PRAGMA defer_foreign_keys=ON;
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('channel','dm')),
    name TEXT,
    user_low INTEGER REFERENCES users(id),
    user_high INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    CHECK ((kind='channel' AND name IS NOT NULL AND user_low IS NULL AND user_high IS NULL) OR (kind='dm' AND name IS NULL AND user_low IS NOT NULL AND user_high IS NOT NULL AND user_low<user_high))
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    author_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0,1)),
    edited_at TEXT,
    created_at TEXT NOT NULL,
    CHECK (deleted=0 OR body='')
);
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE read_markers (
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    message_id INTEGER NOT NULL REFERENCES messages(id),
    PRIMARY KEY (conversation_id,user_id)
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
INSERT INTO "conversations" VALUES(1,'channel','general',NULL,NULL,'2026-11-09T00:48:10.548+00:00');
INSERT INTO "conversations" VALUES(2,'channel','retained',NULL,NULL,'2026-11-09T00:48:10.737+00:00');
INSERT INTO "conversations" VALUES(3,'dm',NULL,2,3,'2026-11-09T00:48:10.743+00:00');
INSERT INTO "messages" VALUES(1,2,2,'Retained channel authorship ☃',0,NULL,'2026-11-09T00:48:10.740+00:00');
INSERT INTO "messages" VALUES(2,3,2,'Edited private text 🌍',0,'2026-11-09T00:48:10.749+00:00','2026-11-09T00:48:10.746+00:00');
INSERT INTO "messages" VALUES(3,3,2,'',1,'2026-11-09T00:48:10.754+00:00','2026-11-09T00:48:10.751+00:00');
INSERT INTO "messages" VALUES(4,3,3,'Retained reply',0,NULL,'2026-11-09T00:48:10.756+00:00');
INSERT INTO "metadata" VALUES('app','chat');
INSERT INTO "metadata" VALUES('instance_name','retained');
INSERT INTO "read_markers" VALUES(3,3,4);
INSERT INTO "sessions" VALUES('ca7fe97941335dfeb73697dc00eab55281b314f1b25635b41c10251200aadfdc',1,'fOnhHTjEBPINOWJm-GzghywJ3qf-_AD1ooka2ABVZ2E','2026-11-16T00:48:10.588+00:00');
INSERT INTO "sessions" VALUES('d9ae2efb8e6384b0481fb06d58037ab55f30c34955fa058a64de81b4bc475e3f',3,'Hcq-4t0tOal4CZnemhs1DfnaxH1VVSeERRX8y8RoeEI','2000-01-01T00:00:00+00:00');
INSERT INTO "users" VALUES(1,'admin','Administrator','scrypt$c0820b5adf7d09c7b36e61848c76a2f6$4243d5e7ef59e7d2915932c1e15b14201d23045a32a1f1f4cfada5552b2ce9dcf717f3cc095667ee5a7bcb5d7d1bdd66040592f354524ccb9bdfd36a640185f5','admin',1,'2026-11-09T00:48:10.548+00:00');
INSERT INTO "users" VALUES(2,'alice','Alice','scrypt$25554a102c4254d7ffe0118a7ca77631$3675b3e2d0412807a5edd7ef77897c545bfee284d69a8b3e243b284b67ab350606b47769a2a7007fc1e9a97dd328ba284098c266280379b65f0490995236ba4c','member',0,'2026-11-09T00:48:10.624+00:00');
INSERT INTO "users" VALUES(3,'bob','Bob','scrypt$d9348c6565a0c6b15bba640d1e679c7c$dbe06b943eb427b384ab0bfdc37e9eb34d1259574c174adb7dcfcbe9d31ccba95020127471cc516ebd6fc5a9ef4bf301cff53d9b3f611c35433ae7c1a684da3a','member',1,'2026-11-09T00:48:10.660+00:00');
CREATE INDEX sessions_user ON sessions(user_id);
CREATE UNIQUE INDEX channel_name ON conversations(name) WHERE kind='channel';
CREATE UNIQUE INDEX dm_pair ON conversations(user_low,user_high) WHERE kind='dm';
CREATE INDEX messages_conversation ON messages(conversation_id,id);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('users',3);
INSERT INTO "sqlite_sequence" VALUES('conversations',3);
INSERT INTO "sqlite_sequence" VALUES('messages',4);
PRAGMA user_version=1;
COMMIT;
