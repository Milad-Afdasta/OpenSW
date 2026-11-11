from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_user, require_write
from ..db import db, now
from ..models import MAX_ID, NewNote, NotePatch, ResourceID, changes
from ..util import bool_fields, nonblank, page, row_or_404

router = APIRouter(prefix="/api/notes")
SUMMARY = "id,title,archived,revision,created_by,created_at,updated_at"


def note_json(row):
    return bool_fields(row, "archived")


@router.get("")
def list_notes(q: str = Query(default="", max_length=200), archived: bool = False, cursor: int = Query(default=0, ge=0, le=MAX_ID), user=Depends(require_user), conn=Depends(db)):
    rows = conn.execute(f"SELECT {SUMMARY} FROM notes WHERE archived=? AND id>? AND instr(lower(title),lower(?))>0 ORDER BY id LIMIT 101", (int(archived), cursor, q)).fetchall()
    return page(rows, note_json)


@router.post("", status_code=201)
def create_note(payload: NewNote, user=Depends(require_write), conn=Depends(db)):
    nonblank(payload.title, "Title")
    timestamp = now()
    with conn:
        result = conn.execute("INSERT INTO notes(title,body,created_by,created_at,updated_at) VALUES (?,?,?,?,?)", (payload.title, payload.body, user["id"], timestamp, timestamp))
    return note_json(row_or_404(conn, "notes", result.lastrowid, "Page"))


@router.get("/{note_id}")
def get_note(note_id: ResourceID, user=Depends(require_user), conn=Depends(db)):
    return note_json(row_or_404(conn, "notes", note_id, "Page"))


@router.patch("/{note_id}")
def update_note(note_id: ResourceID, payload: NotePatch, user=Depends(require_write), conn=Depends(db)):
    try:
        fields = changes(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        current = row_or_404(conn, "notes", note_id, "Page")
        if current["revision"] != payload.expected_revision:
            raise HTTPException(409, "This page changed elsewhere. Keep your draft, then load the latest version.")
        assignments = [f"{key}=?" for key in fields]
        assignments.extend(["revision=revision+1", "updated_at=?"])
        conn.execute(f"UPDATE notes SET {','.join(assignments)} WHERE id=? AND revision=?", [*fields.values(), now(), note_id, payload.expected_revision])
    return note_json(row_or_404(conn, "notes", note_id, "Page"))
