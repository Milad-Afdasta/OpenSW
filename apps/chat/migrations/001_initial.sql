CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('channel','dm')),
    name TEXT,
    user_low INTEGER REFERENCES users(id),
    user_high INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    CHECK ((kind='channel' AND name IS NOT NULL AND user_low IS NULL AND user_high IS NULL) OR (kind='dm' AND name IS NULL AND user_low IS NOT NULL AND user_high IS NOT NULL AND user_low<user_high))
);
CREATE UNIQUE INDEX channel_name ON conversations(name) WHERE kind='channel';
CREATE UNIQUE INDEX dm_pair ON conversations(user_low,user_high) WHERE kind='dm';
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
CREATE INDEX messages_conversation ON messages(conversation_id,id);
CREATE TABLE read_markers (
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    message_id INTEGER NOT NULL REFERENCES messages(id),
    PRIMARY KEY (conversation_id,user_id)
);
