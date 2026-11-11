from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_user, require_write, user_json
from ..db import db, now
from ..models import MAX_ID, Channel, DirectMessage, Message, ReadMarker, ResourceID
from ..util import bool_fields, nonblank, row_or_404

router = APIRouter(prefix="/api/chat")


def conversation(conn, conversation_id: int, user_id: int):
    row = row_or_404(conn, "conversations", conversation_id, "Conversation")
    if row["kind"] == "dm" and user_id not in (row["user_low"], row["user_high"]):
        raise HTTPException(404, "Conversation not found.")
    return row


def conversation_json(conn, row, user_id):
    peer = None
    name = row["name"]
    if row["kind"] == "dm":
        peer_id = row["user_high"] if user_id == row["user_low"] else row["user_low"]
        peer = user_json(row_or_404(conn, "users", peer_id, "Teammate"))
        name = peer["display_name"]
    unread = conn.execute("SELECT count(*) FROM messages WHERE conversation_id=? AND author_id<>? AND deleted=0 AND id>COALESCE((SELECT message_id FROM read_markers WHERE conversation_id=? AND user_id=?),0)", (row["id"], user_id, row["id"], user_id)).fetchone()[0]
    return {"id": row["id"], "kind": row["kind"], "name": name, "peer": peer, "unread_count": unread}


def conversation_page(conn, rows, user_id):
    items = [conversation_json(conn, row, user_id) for row in rows[:100]]
    return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > 100 else None}


def message_json(row):
    return bool_fields(row, "deleted")


@router.get("/channels")
def channels(cursor: int = Query(default=0, ge=0, le=MAX_ID), user=Depends(require_user), conn=Depends(db)):
    rows = conn.execute("SELECT * FROM conversations WHERE kind='channel' AND id>? ORDER BY id LIMIT 101", (cursor,)).fetchall()
    return conversation_page(conn, rows, user["id"])


@router.post("/channels", status_code=201)
def new_channel(payload: Channel, user=Depends(require_write), conn=Depends(db)):
    nonblank(payload.name, "Channel name")
    try:
        with conn:
            result = conn.execute("INSERT INTO conversations(kind,name,created_at) VALUES ('channel',?,?)", (payload.name, now()))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "A channel with that name already exists.") from exc
    return conversation_json(conn, row_or_404(conn, "conversations", result.lastrowid, "Channel"), user["id"])


@router.get("/dms")
def direct_messages(cursor: int = Query(default=0, ge=0, le=MAX_ID), user=Depends(require_user), conn=Depends(db)):
    rows = conn.execute("SELECT * FROM conversations WHERE kind='dm' AND (user_low=? OR user_high=?) AND id>? ORDER BY id LIMIT 101", (user["id"], user["id"], cursor)).fetchall()
    return conversation_page(conn, rows, user["id"])


@router.post("/dms")
def new_direct_message(payload: DirectMessage, user=Depends(require_write), conn=Depends(db)):
    if payload.user_id == user["id"]:
        raise HTTPException(422, "Choose another teammate for a direct message.")
    low, high = sorted((payload.user_id, user["id"]))
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        peer = row_or_404(conn, "users", payload.user_id, "Teammate")
        if not peer["active"]:
            raise HTTPException(422, "Choose an active teammate for a new direct message.")
        conn.execute("INSERT INTO conversations(kind,user_low,user_high,created_at) VALUES ('dm',?,?,?) ON CONFLICT(user_low,user_high) WHERE kind='dm' DO NOTHING", (low, high, now()))
        row = conn.execute("SELECT * FROM conversations WHERE kind='dm' AND user_low=? AND user_high=?", (low, high)).fetchone()
    return conversation_json(conn, row, user["id"])


@router.get("/conversations/{conversation_id}/messages")
def messages(conversation_id: ResourceID, before_id: int | None = Query(default=None, gt=0, le=MAX_ID), q: str = Query(default="", max_length=200), user=Depends(require_user), conn=Depends(db)):
    conversation(conn, conversation_id, user["id"])
    where = "conversation_id=?"
    parameters = [conversation_id]
    if before_id is not None:
        where += " AND id<?"
        parameters.append(before_id)
    if q:
        where += " AND deleted=0 AND instr(lower(body),lower(?))>0"
        parameters.append(q)
    rows = conn.execute(f"SELECT * FROM messages WHERE {where} ORDER BY id DESC LIMIT 101", parameters).fetchall()
    return {"items": [message_json(row) for row in reversed(rows[:100])], "has_more": len(rows) > 100}


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: ResourceID, user=Depends(require_user), conn=Depends(db)):
    return conversation_json(conn, conversation(conn, conversation_id, user["id"]), user["id"])


@router.post("/conversations/{conversation_id}/messages", status_code=201)
def send_message(conversation_id: ResourceID, payload: Message, user=Depends(require_write), conn=Depends(db)):
    nonblank(payload.body, "Message")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        room = conversation(conn, conversation_id, user["id"])
        if room["kind"] == "dm":
            peer_id = room["user_high"] if user["id"] == room["user_low"] else room["user_low"]
            if not row_or_404(conn, "users", peer_id, "Teammate")["active"]:
                raise HTTPException(409, "This teammate is inactive. The conversation history is still available.")
        result = conn.execute("INSERT INTO messages(conversation_id,author_id,body,created_at) VALUES (?,?,?,?)", (conversation_id, user["id"], payload.body, now()))
    return message_json(row_or_404(conn, "messages", result.lastrowid, "Message"))


@router.patch("/messages/{message_id}")
def edit_message(message_id: ResourceID, payload: Message, user=Depends(require_write), conn=Depends(db)):
    nonblank(payload.body, "Message")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = row_or_404(conn, "messages", message_id, "Message")
        conversation(conn, row["conversation_id"], user["id"])
        if row["author_id"] != user["id"]:
            raise HTTPException(403, "You can only edit your own messages.")
        if row["deleted"]:
            raise HTTPException(409, "This message has been deleted.")
        conn.execute("UPDATE messages SET body=?,edited_at=? WHERE id=?", (payload.body, now(), message_id))
    return message_json(row_or_404(conn, "messages", message_id, "Message"))


@router.delete("/messages/{message_id}")
def delete_message(message_id: ResourceID, user=Depends(require_write), conn=Depends(db)):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = row_or_404(conn, "messages", message_id, "Message")
        conversation(conn, row["conversation_id"], user["id"])
        if row["author_id"] != user["id"]:
            raise HTTPException(403, "You can only delete your own messages.")
        conn.execute("UPDATE messages SET body='',deleted=1,edited_at=? WHERE id=?", (now(), message_id))
    return {"ok": True}


@router.post("/conversations/{conversation_id}/read")
def mark_read(conversation_id: ResourceID, payload: ReadMarker, user=Depends(require_write), conn=Depends(db)):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        conversation(conn, conversation_id, user["id"])
        if conn.execute("SELECT id FROM messages WHERE id=? AND conversation_id=?", (payload.message_id, conversation_id)).fetchone() is None:
            raise HTTPException(422, "The read position must reference a message in this conversation.")
        conn.execute("INSERT INTO read_markers(conversation_id,user_id,message_id) VALUES (?,?,?) ON CONFLICT(conversation_id,user_id) DO UPDATE SET message_id=max(read_markers.message_id,excluded.message_id)", (conversation_id, user["id"], payload.message_id))
    return {"ok": True}
