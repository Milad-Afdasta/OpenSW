from __future__ import annotations

import sqlite3

from fastapi import HTTPException


def page(rows: list[sqlite3.Row], transform=dict) -> dict:
    items = [transform(row) for row in rows[:100]]
    return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > 100 else None}


def row_or_404(conn: sqlite3.Connection, table: str, resource_id: int, label: str):
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (resource_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"{label} not found.")
    return row


def nonblank(value: str, label: str) -> str:
    if not value.strip():
        raise HTTPException(422, f"{label} cannot be blank.")
    return value


def bool_fields(row: sqlite3.Row | dict, *fields: str) -> dict:
    result = dict(row)
    for field in fields:
        result[field] = bool(result[field])
    return result
