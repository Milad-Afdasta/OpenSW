CREATE TABLE task_statuses (id TEXT PRIMARY KEY);
CREATE TABLE task_priorities (id TEXT PRIMARY KEY);
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1)),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
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
CREATE INDEX tasks_project ON tasks(project_id,id);
CREATE TABLE comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    body TEXT NOT NULL,
    author_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE INDEX comments_task ON comments(task_id,id);
