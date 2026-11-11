from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from .config import CATALOG
from .db import connect, db, now
from .models import MAX_ID, Login, NewUser, PasswordChange, ResourceID, UserActive

router = APIRouter(prefix="/api")
USER_FIELDS = "id,username,display_name,role,active"


class LoginAdmission:
    """One-worker, bounded pressure and hashing ownership for public sign-in."""

    WINDOW = 60
    GLOBAL_LIMIT = 120
    PEER_LIMIT = 60
    NAME_LIMIT = 15
    HASH_SLOTS = 2

    def __init__(self):
        self.lock = threading.Lock()
        self.events = deque()
        self.peers = {}
        self.names = {}
        self.hash_slots = threading.BoundedSemaphore(self.HASH_SLOTS)

    @contextmanager
    def attempt(self, peer: str, name: str):
        with self.lock:
            current = time.monotonic()
            while self.events and self.events[0][0] <= current - self.WINDOW:
                _, old_peer, old_name = self.events.popleft()
                for counts, key in ((self.peers, old_peer), (self.names, old_name)):
                    counts[key] -= 1
                    if counts[key] == 0:
                        del counts[key]
            retry_at = None
            if len(self.events) >= self.GLOBAL_LIMIT:
                retry_at = self.events[0][0] + self.WINDOW
            elif self.peers.get(peer, 0) >= self.PEER_LIMIT:
                retry_at = next(at for at, item_peer, _ in self.events if item_peer == peer) + self.WINDOW
            elif self.names.get(name, 0) >= self.NAME_LIMIT:
                retry_at = next(at for at, _, item_name in self.events if item_name == name) + self.WINDOW
            if retry_at is not None:
                raise HTTPException(429, "Too many sign-in attempts. Wait before trying again.", headers={"Retry-After": str(max(1, math.ceil(retry_at - current)))})
            # Only accepted pressure entries allocate keys, so rotating names or
            # peers cannot grow state beyond the bounded global window.
            self.events.append((current, peer, name))
            self.peers[peer] = self.peers.get(peer, 0) + 1
            self.names[name] = self.names.get(name, 0) + 1
        if not self.hash_slots.acquire(blocking=False):
            raise HTTPException(429, "Sign-in is busy. Try again shortly.", headers={"Retry-After": "1"})
        try:
            yield
        finally:
            self.hash_slots.release()


def require_origin(request: Request) -> None:
    origin = request.headers.get("Origin")
    if origin is not None and origin != request.app.state.settings.public_url:
        raise HTTPException(403, "This action must come from this OpenSW application's address.")


def validate_username(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9_.-]{3,32}", value):
        raise ValueError("Username must contain 3–32 lowercase letters, numbers, periods, underscores or hyphens.")
    return value


def validate_password(value: str) -> str:
    if not isinstance(value, str) or not 12 <= len(value) <= 256:
        raise ValueError("Password must contain 12–256 characters.")
    return value


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, maxmem=64 * 1024 * 1024).hex()
    return f"scrypt${salt}${digest}"


def check_password(password: str, encoded: str) -> bool:
    try:
        method, salt, expected = encoded.split("$")
        if method != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, maxmem=64 * 1024 * 1024).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def user_json(row: sqlite3.Row | dict) -> dict:
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"], "role": row["role"], "active": bool(row["active"])}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def require_user(request: Request, conn=Depends(db)) -> dict:
    token = request.cookies.get(request.app.state.settings.cookie_name, "")
    if not token or len(token) > 256:
        raise HTTPException(401, "Sign in to continue.")
    row = conn.execute("SELECT u.*,s.csrf_token,s.token_hash,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1", (token_hash(token), now())).fetchone()
    if row is None:
        raise HTTPException(401, "Your session has ended. Sign in again.")
    request.state.session = dict(row)
    return user_json(row)


def require_write(request: Request, user=Depends(require_user)) -> dict:
    require_origin(request)
    received = request.headers.get("X-CSRF-Token", "")
    if not received or not received.isascii() or not hmac.compare_digest(received, request.state.session["csrf_token"]):
        raise HTTPException(403, "This action needs a current session. Refresh and try again.")
    return user


class AuthenticatedWriteRoute(APIRoute):
    """Resolve the canonical session before FastAPI consumes a multipart body."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        def admit(request: Request):
            with closing(connect(request.app.state.settings)) as conn:
                require_write(request, require_user(request, conn))

        async def admitted(request: Request):
            await run_in_threadpool(admit, request)
            return await handler(request)

        return admitted


def require_admin(user=Depends(require_write)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Only an administrator can manage teammates.")
    return user


def session_json(request: Request, user: dict) -> dict:
    return {"user": user, "csrf_token": request.state.session["csrf_token"], "app": request.app.state.settings.app, "team_name": request.app.state.settings.team_name, "catalog": CATALOG}


@router.get("/me")
def me(request: Request, user=Depends(require_user)):
    return session_json(request, user)


@router.post("/auth/login")
def login(payload: Login, request: Request, response: Response, conn=Depends(db)):
    # Peer identity is the transport peer, never an arbitrary forwarding header.
    peer = request.client.host if request.client else "local"
    with request.app.state.login_admission.attempt(peer, payload.username):
        row = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (payload.username,)).fetchone()
        if row is None:
            # Keep unknown-user checks on the same deliberately expensive path.
            hash_password(payload.password)
            raise HTTPException(401, "Username or password is incorrect.")
        if not check_password(payload.password, row["password_hash"]):
            raise HTTPException(401, "Username or password is incorrect.")
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="milliseconds")
    old_token = request.cookies.get(request.app.state.settings.cookie_name)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        # Hashing can overlap a password change or account deactivation. The
        # session is issued only against the current owner row under the lock.
        confirmed = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (row["id"],)).fetchone()
        if confirmed is None or confirmed["password_hash"] != row["password_hash"]:
            raise HTTPException(401, "Username or password is incorrect.")
        conn.execute("DELETE FROM sessions WHERE expires_at<=?", (now(),))
        if old_token:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(old_token),))
        conn.execute("INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at) VALUES (?,?,?,?)", (token_hash(token), row["id"], csrf, expiry))
    request.state.session = {"csrf_token": csrf}
    response.set_cookie(request.app.state.settings.cookie_name, token, max_age=7 * 86400, httponly=True, secure=request.app.state.settings.secure_cookie, samesite="strict", path="/")
    return session_json(request, user_json(confirmed))


@router.post("/auth/logout")
def logout(request: Request, response: Response, user=Depends(require_write), conn=Depends(db)):
    with conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (request.state.session["token_hash"],))
    response.delete_cookie(request.app.state.settings.cookie_name, path="/", httponly=True, secure=request.app.state.settings.secure_cookie, samesite="strict")
    return {"ok": True}


@router.post("/auth/password")
def password_change(payload: PasswordChange, request: Request, response: Response, user=Depends(require_write), conn=Depends(db)):
    try:
        validate_password(payload.new_password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    encoded_new = hash_password(payload.new_password)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        encoded = conn.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()[0]
        if not check_password(payload.current_password, encoded):
            raise HTTPException(403, "The current password is incorrect.")
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (encoded_new, user["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    response.delete_cookie(request.app.state.settings.cookie_name, path="/", httponly=True, secure=request.app.state.settings.secure_cookie, samesite="strict")
    return {"ok": True}


@router.get("/users")
def users(cursor: int = Query(default=0, ge=0, le=MAX_ID), user=Depends(require_user), conn=Depends(db)):
    rows = conn.execute(f"SELECT {USER_FIELDS} FROM users WHERE id>? ORDER BY id LIMIT 101", (cursor,)).fetchall()
    items = [user_json(row) for row in rows[:100]]
    return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > 100 else None}


@router.post("/users", status_code=201)
def new_user(payload: NewUser, user=Depends(require_admin), conn=Depends(db)):
    try:
        validate_username(payload.username)
        validate_password(payload.password)
        if not payload.display_name.strip():
            raise ValueError("Display name cannot be blank.")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        with conn:
            result = conn.execute("INSERT INTO users(username,display_name,password_hash,role,created_at) VALUES (?,?,?,'member',?)", (payload.username, payload.display_name, hash_password(payload.password), now()))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "That username is already in use.") from exc
    return user_json(conn.execute(f"SELECT {USER_FIELDS} FROM users WHERE id=?", (result.lastrowid,)).fetchone())


@router.patch("/users/{user_id}")
def update_user(user_id: ResourceID, payload: UserActive, user=Depends(require_admin), conn=Depends(db)):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        target = conn.execute(f"SELECT {USER_FIELDS} FROM users WHERE id=?", (user_id,)).fetchone()
        if target is None:
            raise HTTPException(404, "Teammate not found.")
        if not payload.active and target["role"] == "admin" and target["active"]:
            count = conn.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
            if count <= 1:
                raise HTTPException(409, "The last administrator must stay active.")
        conn.execute("UPDATE users SET active=? WHERE id=?", (int(payload.active), user_id))
        if not payload.active:
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    return user_json(conn.execute(f"SELECT {USER_FIELDS} FROM users WHERE id=?", (user_id,)).fetchone())
