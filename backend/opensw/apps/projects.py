from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_user, require_write
from ..db import db, now
from ..models import MAX_ID, Comment, NewProject, NewTask, ProjectPatch, ResourceID, TaskPatch, changes
from ..util import bool_fields, nonblank, page, row_or_404

router = APIRouter(prefix="/api")
TASK_SUMMARY = "id,project_id,title,status,priority,assignee_id,due_date,archived,revision,created_by,created_at,updated_at"


def resource_json(row):
    return bool_fields(row, "archived")


def active_project(conn, project_id):
    row = row_or_404(conn, "projects", project_id, "Project")
    if row["archived"]:
        raise HTTPException(409, "Restore this project before changing its tasks or comments.")
    return row


def active_assignee(conn, user_id):
    if user_id is not None and conn.execute("SELECT id FROM users WHERE id=? AND active=1", (user_id,)).fetchone() is None:
        raise HTTPException(422, "Choose an active teammate as assignee.")


@router.get("/projects")
def list_projects(archived: bool = False, q: str = Query(default="", max_length=200), cursor: int = Query(default=0, ge=0, le=MAX_ID), user=Depends(require_user), conn=Depends(db)):
    rows = conn.execute("SELECT * FROM projects WHERE archived=? AND id>? AND instr(lower(name),lower(?))>0 ORDER BY id LIMIT 101", (int(archived), cursor, q)).fetchall()
    return page(rows, resource_json)


@router.post("/projects", status_code=201)
def create_project(payload: NewProject, user=Depends(require_write), conn=Depends(db)):
    nonblank(payload.name, "Project name")
    timestamp = now()
    with conn:
        result = conn.execute("INSERT INTO projects(name,description,created_by,created_at,updated_at) VALUES (?,?,?,?,?)", (payload.name, payload.description, user["id"], timestamp, timestamp))
    return resource_json(row_or_404(conn, "projects", result.lastrowid, "Project"))


@router.patch("/projects/{project_id}")
def update_project(project_id: ResourceID, payload: ProjectPatch, user=Depends(require_write), conn=Depends(db)):
    try:
        fields = changes(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row_or_404(conn, "projects", project_id, "Project")
        if fields:
            conn.execute(f"UPDATE projects SET {','.join(f'{key}=?' for key in fields)},updated_at=? WHERE id=?", [*fields.values(), now(), project_id])
    return resource_json(row_or_404(conn, "projects", project_id, "Project"))


@router.get("/projects/{project_id}")
def get_project(project_id: ResourceID, user=Depends(require_user), conn=Depends(db)):
    return resource_json(row_or_404(conn, "projects", project_id, "Project"))


@router.get("/projects/{project_id}/tasks")
def list_tasks(project_id: ResourceID, archived: bool = False, q: str = Query(default="", max_length=200), assignee_id: int | None = Query(default=None, gt=0, le=MAX_ID), cursor: int = Query(default=0, ge=0, le=MAX_ID), user=Depends(require_user), conn=Depends(db)):
    row_or_404(conn, "projects", project_id, "Project")
    where = "project_id=? AND archived=? AND id>? AND instr(lower(title),lower(?))>0"
    parameters = [project_id, int(archived), cursor, q]
    if assignee_id is not None:
        where += " AND assignee_id=?"
        parameters.append(assignee_id)
    rows = conn.execute(f"SELECT {TASK_SUMMARY} FROM tasks WHERE {where} ORDER BY id LIMIT 101", parameters).fetchall()
    return page(rows, resource_json)


@router.post("/projects/{project_id}/tasks", status_code=201)
def create_task(project_id: ResourceID, payload: NewTask, user=Depends(require_write), conn=Depends(db)):
    nonblank(payload.title, "Task title")
    timestamp = now()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        active_project(conn, project_id)
        active_assignee(conn, payload.assignee_id)
        result = conn.execute("INSERT INTO tasks(project_id,title,description,status,priority,assignee_id,due_date,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (project_id, payload.title, payload.description, payload.status, payload.priority, payload.assignee_id, payload.due_date, user["id"], timestamp, timestamp))
    return resource_json(row_or_404(conn, "tasks", result.lastrowid, "Task"))


@router.get("/tasks/{task_id}")
def get_task(task_id: ResourceID, user=Depends(require_user), conn=Depends(db)):
    return resource_json(row_or_404(conn, "tasks", task_id, "Task"))


@router.patch("/tasks/{task_id}")
def update_task(task_id: ResourceID, payload: TaskPatch, user=Depends(require_write), conn=Depends(db)):
    try:
        fields = changes(payload, {"assignee_id", "due_date"})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        current = row_or_404(conn, "tasks", task_id, "Task")
        active_project(conn, current["project_id"])
        if current["revision"] != payload.expected_revision:
            raise HTTPException(409, "This task changed elsewhere. Keep your draft, then load the latest version.")
        if "assignee_id" in fields and fields["assignee_id"] != current["assignee_id"]:
            active_assignee(conn, fields["assignee_id"])
        assignments = [f"{key}=?" for key in fields]
        assignments.extend(["revision=revision+1", "updated_at=?"])
        conn.execute(f"UPDATE tasks SET {','.join(assignments)} WHERE id=? AND revision=?", [*fields.values(), now(), task_id, payload.expected_revision])
    return resource_json(row_or_404(conn, "tasks", task_id, "Task"))


@router.get("/tasks/{task_id}/comments")
def list_comments(task_id: ResourceID, cursor: int = Query(default=0, ge=0, le=MAX_ID), user=Depends(require_user), conn=Depends(db)):
    row_or_404(conn, "tasks", task_id, "Task")
    return page(conn.execute("SELECT * FROM comments WHERE task_id=? AND id>? ORDER BY id LIMIT 101", (task_id, cursor)).fetchall())


@router.post("/tasks/{task_id}/comments", status_code=201)
def add_comment(task_id: ResourceID, payload: Comment, user=Depends(require_write), conn=Depends(db)):
    nonblank(payload.body, "Comment")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        task = row_or_404(conn, "tasks", task_id, "Task")
        active_project(conn, task["project_id"])
        if task["archived"]:
            raise HTTPException(409, "Restore this task before adding comments.")
        result = conn.execute("INSERT INTO comments(task_id,body,author_id,created_at) VALUES (?,?,?,?)", (task_id, payload.body, user["id"], now()))
    return dict(row_or_404(conn, "comments", result.lastrowid, "Comment"))
