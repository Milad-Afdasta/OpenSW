from __future__ import annotations

import errno
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from ..auth import AuthenticatedWriteRoute, require_user, require_write
from ..db import db, now
from ..models import MAX_ID, Folder, Rename, ResourceID
from ..util import page, row_or_404

router = APIRouter(prefix="/api/files")
upload_router = APIRouter(route_class=AuthenticatedWriteRoute)


def filename(value: str) -> str:
    if not value or not value.strip() or len(value) > 180 or value in {".", ".."} or any(char in value for char in "/\\") or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise HTTPException(422, "Use a filename of 1–180 characters without slashes or control characters.")
    return value


def file_json(row):
    return {key: row[key] for key in ("id", "name", "kind", "parent_id", "size", "created_by", "created_at", "updated_at", "deleted_at")}


def active_parent(conn, parent_id):
    if parent_id is None:
        return None
    row = row_or_404(conn, "files", parent_id, "Folder")
    if row["kind"] != "folder" or row["deleted_at"] is not None:
        raise HTTPException(409, "Choose an active folder for this file.")
    # Parents cannot move, and folders with active children cannot be trashed. Checking the
    # entire chain also fails closed if storage was changed outside the API.
    if row["parent_id"] is not None:
        active_parent(conn, row["parent_id"])
    return row


def active_item(conn, file_id):
    row = row_or_404(conn, "files", file_id, "File")
    if row["deleted_at"] is not None:
        raise HTTPException(404, "This item is in Trash.")
    active_parent(conn, row["parent_id"])
    return row


@router.get("")
def list_files(parent_id: int | None = Query(default=None, gt=0, le=MAX_ID), trash: bool = False, q: str = Query(default="", max_length=200), cursor: int = Query(default=0, ge=0, le=MAX_ID), user=Depends(require_user), conn=Depends(db)):
    breadcrumbs = [{"id": None, "name": "Files"}]
    if parent_id is not None:
        folder = active_parent(conn, parent_id)
        chain = []
        while folder is not None:
            chain.append({"id": folder["id"], "name": folder["name"]})
            folder = active_parent(conn, folder["parent_id"])
        breadcrumbs.extend(reversed(chain))
    parameters = [cursor]
    where = "id>? AND deleted_at IS " + ("NOT NULL" if trash else "NULL")
    if q:
        where += " AND instr(lower(name),lower(?))>0"
        parameters.append(q)
    elif not trash:
        where += " AND parent_id IS ?"
        parameters.append(parent_id)
    result = page(conn.execute(f"SELECT * FROM files WHERE {where} ORDER BY id LIMIT 101", parameters).fetchall(), file_json)
    result["breadcrumbs"] = breadcrumbs
    result["usage_bytes"] = conn.execute("SELECT COALESCE(sum(size),0) FROM files WHERE kind='file'").fetchone()[0]
    return result


@router.post("/folders", status_code=201)
def create_folder(payload: Folder, user=Depends(require_write), conn=Depends(db)):
    name = filename(payload.name)
    timestamp = now()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            active_parent(conn, payload.parent_id)
            result = conn.execute("INSERT INTO files(name,kind,parent_id,created_by,created_at,updated_at) VALUES (?,'folder',?,?,?,?)", (name, payload.parent_id, user["id"], timestamp, timestamp))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "An item with that name already exists in this folder.") from exc
    return file_json(row_or_404(conn, "files", result.lastrowid, "Folder"))


@upload_router.post("/upload", status_code=201)
def upload_file(request: Request, file: UploadFile = File(...), parent_id: int | None = Form(default=None, gt=0, le=MAX_ID), user=Depends(require_write), conn=Depends(db)):
    name = filename(file.filename or "")
    active_parent(conn, parent_id)
    blob_dir = request.app.state.settings.data_dir / "blobs"
    key = uuid.uuid4().hex
    final_path = blob_dir / key
    temporary: Path | None = None
    committed = False
    commit_attempted = False
    size = 0
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=blob_dir)
        temporary = Path(temporary_name)
        with os.fdopen(fd, "wb") as target:
            while chunk := file.file.read(64 * 1024):
                size += len(chunk)
                if size > request.app.state.settings.max_upload_mb * 1024 * 1024:
                    raise HTTPException(413, "This file is larger than the configured upload limit.")
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        conn.execute("BEGIN IMMEDIATE")
        # Admission precedes body parsing, but a session or account can change
        # during the transfer. Confirm authorization under the mutation lock.
        user = require_write(request, require_user(request, conn))
        active_parent(conn, parent_id)
        timestamp = now()
        # Fail the sibling-name predicate before creating a retained blob.
        duplicate = conn.execute("SELECT id FROM files WHERE parent_id IS ? AND name=? AND deleted_at IS NULL", (parent_id, name)).fetchone()
        if duplicate:
            raise HTTPException(409, "An item with that name already exists in this folder.")
        os.replace(temporary, final_path)
        temporary = None
        directory_fd = os.open(blob_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        result = conn.execute("INSERT INTO files(name,kind,parent_id,blob_key,size,created_by,created_at,updated_at) VALUES (?,'file',?,?,?,?,?,?)", (name, parent_id, key, size, user["id"], timestamp, timestamp))
        row = file_json(row_or_404(conn, "files", result.lastrowid, "File"))
        commit_attempted = True
        conn.commit()
        committed = True
        return row
    except BaseException as exc:
        # COMMIT can succeed before its acknowledgment fails. Only a known
        # rollback permits compensation; committed/unknown data is retained.
        rollback_proven = not commit_attempted
        if conn.in_transaction:
            try:
                conn.rollback()
                rollback_proven = True
            except sqlite3.Error:
                rollback_proven = False
        if not committed and rollback_proven and final_path.exists():
            final_path.unlink()
        if isinstance(exc, sqlite3.IntegrityError):
            raise HTTPException(409, "The destination changed. Refresh the folder before uploading again.") from exc
        if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            raise HTTPException(507, "The server has run out of storage. Contact your administrator.") from exc
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        file.file.close()


router.include_router(upload_router)


@router.get("/{file_id}")
def get_file(file_id: ResourceID, user=Depends(require_user), conn=Depends(db)):
    return file_json(active_item(conn, file_id))


@router.patch("/{file_id}")
def rename_file(file_id: ResourceID, payload: Rename, user=Depends(require_write), conn=Depends(db)):
    name = filename(payload.name)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            active_item(conn, file_id)
            conn.execute("UPDATE files SET name=?,updated_at=? WHERE id=?", (name, now(), file_id))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "An item with that name already exists in this folder.") from exc
    return file_json(row_or_404(conn, "files", file_id, "File"))


@router.delete("/{file_id}")
def trash_file(file_id: ResourceID, user=Depends(require_write), conn=Depends(db)):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        current = active_item(conn, file_id)
        if current["kind"] == "folder" and conn.execute("SELECT id FROM files WHERE parent_id=? AND deleted_at IS NULL LIMIT 1", (file_id,)).fetchone():
            raise HTTPException(409, "Move this folder's active items to Trash before moving the folder.")
        timestamp = now()
        conn.execute("UPDATE files SET deleted_at=?,updated_at=? WHERE id=?", (timestamp, timestamp, file_id))
    return {"ok": True}


@router.post("/{file_id}/restore")
def restore_file(file_id: ResourceID, user=Depends(require_write), conn=Depends(db)):
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            current = row_or_404(conn, "files", file_id, "Item")
            active_parent(conn, current["parent_id"])
            conn.execute("UPDATE files SET deleted_at=NULL,updated_at=? WHERE id=?", (now(), file_id))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Another active item uses this name. Rename it before restoring this item.") from exc
    return file_json(row_or_404(conn, "files", file_id, "Item"))


@router.get("/{file_id}/download")
def download_file(file_id: ResourceID, request: Request, user=Depends(require_user), conn=Depends(db)):
    row = active_item(conn, file_id)
    if row["kind"] != "file":
        raise HTTPException(409, "Folders cannot be downloaded as a file.")
    path = request.app.state.settings.data_dir / "blobs" / row["blob_key"]
    if not path.is_file():
        raise HTTPException(503, "This file's stored content is unavailable. Contact your administrator.")
    return FileResponse(path, filename=row["name"], media_type="application/octet-stream", content_disposition_type="attachment", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
