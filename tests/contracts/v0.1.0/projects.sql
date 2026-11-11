-- Synthetic v0.1.0 producer fixture; no operator data.
PRAGMA foreign_keys=ON;
BEGIN TRANSACTION;
PRAGMA defer_foreign_keys=ON;
CREATE TABLE comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    body TEXT NOT NULL,
    author_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1)),
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
CREATE TABLE task_priorities (id TEXT PRIMARY KEY);
CREATE TABLE task_statuses (id TEXT PRIMARY KEY);
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL REFERENCES task_statuses(id),
    priority TEXT NOT NULL REFERENCES task_priorities(id),
    assignee_id INTEGER REFERENCES users(id),
    due_date TEXT,
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1)),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
INSERT INTO "comments" VALUES(1,1,'Retained disabled authorship',2,'2026-11-09T00:48:10.229+00:00');
INSERT INTO "metadata" VALUES('app','projects');
INSERT INTO "metadata" VALUES('instance_name','retained');
INSERT INTO "projects" VALUES(1,'Current project','Retained complete project ☃',0,2,'2026-11-09T00:48:10.191+00:00','2026-11-09T00:48:10.191+00:00');
INSERT INTO "projects" VALUES(2,'Archived project','Historical project body',1,2,'2026-11-09T00:48:10.194+00:00','2026-11-09T00:48:10.197+00:00');
INSERT INTO "sessions" VALUES('64f0fdfaaa4ed117226fabdb7d2a18226dd65735a1b1d22f2e7cca6c1180519a',1,'urHQvXbZmmLrBvnJdEAtTKOQ1nQzrIsAsyHQZTvyXxU','2026-11-16T00:48:10.042+00:00');
INSERT INTO "sessions" VALUES('ae5f91374d6feef74313ad8dde35176bb3445f416b41c62bbd649131a078ead0',3,'ssFTOzp1VFAWEH8-KWSuE6b2FFMw3MGzMDXNgcT8t7Q','2000-01-01T00:00:00+00:00');
INSERT INTO "task_priorities" VALUES('low');
INSERT INTO "task_priorities" VALUES('normal');
INSERT INTO "task_priorities" VALUES('high');
INSERT INTO "task_statuses" VALUES('todo');
INSERT INTO "task_statuses" VALUES('doing');
INSERT INTO "task_statuses" VALUES('done');
INSERT INTO "tasks" VALUES(1,1,'Revision two','Full retained description 🌍','todo','low',2,'2030-01-15',0,2,2,'2026-11-09T00:48:10.200+00:00','2026-11-09T00:48:10.224+00:00');
INSERT INTO "tasks" VALUES(2,1,'todo-normal','Full retained description 🌍','todo','normal',2,'2030-01-15',0,1,2,'2026-11-09T00:48:10.202+00:00','2026-11-09T00:48:10.202+00:00');
INSERT INTO "tasks" VALUES(3,1,'todo-high','Full retained description 🌍','todo','high',2,'2030-01-15',0,1,2,'2026-11-09T00:48:10.205+00:00','2026-11-09T00:48:10.205+00:00');
INSERT INTO "tasks" VALUES(4,1,'doing-low','Full retained description 🌍','doing','low',2,'2030-01-15',0,1,2,'2026-11-09T00:48:10.208+00:00','2026-11-09T00:48:10.208+00:00');
INSERT INTO "tasks" VALUES(5,1,'doing-normal','Full retained description 🌍','doing','normal',2,'2030-01-15',0,1,2,'2026-11-09T00:48:10.210+00:00','2026-11-09T00:48:10.210+00:00');
INSERT INTO "tasks" VALUES(6,1,'doing-high','Full retained description 🌍','doing','high',2,'2030-01-15',0,1,2,'2026-11-09T00:48:10.213+00:00','2026-11-09T00:48:10.213+00:00');
INSERT INTO "tasks" VALUES(7,1,'done-low','Full retained description 🌍','done','low',2,'2030-01-15',0,1,2,'2026-11-09T00:48:10.216+00:00','2026-11-09T00:48:10.216+00:00');
INSERT INTO "tasks" VALUES(8,1,'done-normal','Full retained description 🌍','done','normal',2,'2030-01-15',0,1,2,'2026-11-09T00:48:10.218+00:00','2026-11-09T00:48:10.218+00:00');
INSERT INTO "tasks" VALUES(9,1,'done-high','Full retained description 🌍','done','high',2,'2030-01-15',1,2,2,'2026-11-09T00:48:10.221+00:00','2026-11-09T00:48:10.226+00:00');
INSERT INTO "users" VALUES(1,'admin','Administrator','scrypt$100720e6132d40c5c1e6344f5984a853$7566a5fe31a2f8007b0d815b18656225429de9629a0d96d99ac62fef8525868f851345202d9050023b7dbb74deb4aa049da364194c7155ac58536c59c696870c','admin',1,'2026-11-09T00:48:10.006+00:00');
INSERT INTO "users" VALUES(2,'alice','Alice','scrypt$16aa74d039570a60905d4961afb54a90$db1d92ba818c7fc02e4e2cc7ce49f8d0ebc311b78ba027a953b5625e3f8b5abb48257cda99c9c472d0f721617e6b60f01b0970ec8909c87d911a361e8d99d1e5','member',0,'2026-11-09T00:48:10.078+00:00');
INSERT INTO "users" VALUES(3,'bob','Bob','scrypt$9cdf1cbc0ae08662ebebb5d72dce7caf$7f3209cb244ae10417c343dd50a616af43618e01ef6126ba789d614ccd38c6cf3aaff51c68c31b38a3b70c2c8a61c8a0ea5dc199ae110f64e2e1fe26872e69bf','member',1,'2026-11-09T00:48:10.114+00:00');
CREATE INDEX sessions_user ON sessions(user_id);
CREATE INDEX tasks_project ON tasks(project_id,id);
CREATE INDEX comments_task ON comments(task_id,id);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('users',3);
INSERT INTO "sqlite_sequence" VALUES('projects',2);
INSERT INTO "sqlite_sequence" VALUES('tasks',9);
INSERT INTO "sqlite_sequence" VALUES('comments',1);
PRAGMA user_version=1;
COMMIT;
