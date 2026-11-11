from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from fastapi import Request

from .config import CATALOG, ROOT, Settings


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def connect(settings: Settings) -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if conn.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
        conn.close()
        raise RuntimeError("OpenSW requires SQLite rollback journaling for this release.")
    return conn


def db(request: Request):
    with closing(connect(request.app.state.settings)) as conn:
        yield conn


def initialize(settings: Settings) -> None:
    from .auth import hash_password, validate_password, validate_username

    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(settings.data_dir, 0o700)
    if settings.app == "files":
        (settings.data_dir / "blobs").mkdir(mode=0o700, exist_ok=True)
    bootstrap = settings.data_dir / "state" / "bootstrap.json"
    with closing(connect(settings)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables and "metadata" not in tables:
            raise RuntimeError("The database has no OpenSW ownership record; refusing to adopt it.")
        initialized = "metadata" in tables
        if initialized:
            metadata = dict(conn.execute("SELECT key,value FROM metadata"))
            if metadata != {"app": settings.app, "instance_name": settings.instance_name} or conn.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise RuntimeError("The database belongs to another app/instance or schema version.")
            if conn.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] < 1:
                raise RuntimeError("The database has no active administrator; recovery is required.")
        else:
            try:
                admin = json.loads(bootstrap.read_text())
                if set(admin) != {"username", "password"}:
                    raise ValueError()
                validate_username(admin["username"])
                validate_password(admin["password"])
            except (OSError, ValueError, TypeError) as exc:
                raise RuntimeError("A new installation requires a valid one-time administrator bootstrap file.") from exc
            # Schema, ownership, catalog projection, and first user commit together.
            sql = (ROOT / "backend" / "opensw" / "schema" / "core.sql").read_text()
            sql += "\n" + (ROOT / "apps" / settings.app / "migrations" / "001_initial.sql").read_text()
            try:
                conn.executescript("BEGIN EXCLUSIVE;\n" + sql)
                conn.executemany("INSERT INTO metadata(key,value) VALUES (?,?)", [("app", settings.app), ("instance_name", settings.instance_name)])
                if settings.app == "projects":
                    for table, key in [("task_statuses", "task_statuses"), ("task_priorities", "task_priorities")]:
                        conn.executemany(f"INSERT INTO {table}(id) VALUES (?)", [(item["id"],) for item in CATALOG[key]])
                conn.execute("INSERT INTO users(username,display_name,password_hash,role,created_at) VALUES (?,?,?,'admin',?)", (admin["username"], "Administrator", hash_password(admin["password"]), now()))
                if settings.app == "chat":
                    conn.execute("INSERT INTO conversations(kind,name,created_at) VALUES ('channel','general',?)", (now(),))
                conn.commit()
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise
        if settings.app == "projects":
            for table, key in [("task_statuses", "task_statuses"), ("task_priorities", "task_priorities")]:
                actual = {row[0] for row in conn.execute(f"SELECT id FROM {table}")}
                if actual != {item["id"] for item in CATALOG[key]}:
                    raise RuntimeError("The stored task catalog differs from this release; a migration is required.")
    os.chmod(settings.db_path, 0o600)
    # A post-commit cleanup failure leaves an already initialized app, never a reset.
    if bootstrap.exists():
        bootstrap.unlink()
