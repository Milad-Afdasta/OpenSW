"""Actual v0.1 producer fixtures remain valid under the current app owner.

The checked-in synthetic SQL and complete JSON came from the released backend
in an isolated subprocess, not a reimplementation of its responses. No browser
session cookie was exported. This public password belongs only to these tests.
"""

import hashlib
import json
from pathlib import Path
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from opensw.config import CATALOG
from opensw.db import now
from opensw.main import create_app


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "contracts" / "v0.1.0"
PASSWORD = "OpenSW-retained-fixture-9!"
APPS = tuple(item["id"] for item in CATALOG["apps"])


def stored_rows(path, *, sessions=True):
    with sqlite3.connect(path) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {table: conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() for table in tables if sessions or table != "sessions"}


def schema(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()


def test_released_fixture_provenance_and_content_digests():
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    assert manifest["release"] == "v0.1.0"
    assert manifest["release_commit"] == "85ac6e2815d740d15c60a89a14f800bc02d63790"
    assert manifest["synthetic"] is True and manifest["exported_session_cookies"] is False
    assert manifest["catalog"]["version"] == "0.1.0"
    assert re.fullmatch(r"\d+\.\d+\.\d+", CATALOG["version"])
    assert {key: value for key, value in manifest["catalog"].items() if key != "version"} == {key: value for key, value in CATALOG.items() if key != "version"}
    assert set(manifest["sha256"]) == {f"{app}{suffix}" for app in APPS for suffix in (".sql", ".json", "-openapi.json")}
    for filename, digest in manifest["sha256"].items():
        assert hashlib.sha256((FIXTURES / filename).read_bytes()).hexdigest() == digest, filename


@pytest.mark.parametrize("app_id", APPS)
def test_every_released_api_operation_and_schema_is_unchanged(tmp_path, app_id):
    frozen = json.loads((FIXTURES / f"{app_id}-openapi.json").read_text())
    current = create_app(app_id, tmp_path).openapi()
    assert frozen["info"]["version"] == "0.1.0"
    assert current["info"]["version"] == CATALOG["version"]
    assert current["components"] == frozen["components"]
    for path, operations in frozen["paths"].items():
        for method, operation in operations.items():
            assert current["paths"][path][method] == operation, (method, path)


@pytest.mark.parametrize("app_id", APPS)
def test_current_app_reopens_actual_released_rows_and_replays_complete_producer_json(tmp_path, app_id):
    fixture = json.loads((FIXTURES / f"{app_id}.json").read_text())
    assert set(fixture["config"]) == {"app", "instance_name", "team_name", "public_url", "max_upload_mb"}
    state = tmp_path / "state"
    state.mkdir()
    (state / "config.json").write_text(json.dumps(fixture["config"]))
    database = tmp_path / "opensw.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript((FIXTURES / f"{app_id}.sql").read_text())
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert dict(conn.execute("SELECT key,value FROM metadata")) == {"app": app_id, "instance_name": "retained"}
        users = conn.execute("SELECT id,username,password_hash,active FROM users ORDER BY id").fetchall()
        assert [(row[1], row[3]) for row in users] == [("admin", 1), ("alice", 0), ("bob", 1)]
        assert all(row[2].startswith("scrypt$") for row in users)
        old_expired_tokens = {row[0] for row in conn.execute("SELECT token_hash FROM sessions WHERE expires_at<=?", (now(),))}
        assert old_expired_tokens
    if app_id == "files":
        blobs = tmp_path / "blobs"
        blobs.mkdir()
        for key, text in fixture["blobs"].items():
            assert len(key) == 32 and all(c in "0123456789abcdef" for c in key)
            (blobs / key).write_bytes(text.encode())
    before_rows, before_schema = stored_rows(database), schema(database)
    with sqlite3.connect(":memory:") as master:
        master.executescript((ROOT / "apps" / app_id / "schema.sql").read_text())
        assert master.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall() == before_schema
        assert master.execute("PRAGMA user_version").fetchone()[0] == 1
    application = create_app(app_id, tmp_path)
    with TestClient(application) as lifecycle:
        # Startup may inspect/chmod retained storage; it must never reseed,
        # transform or migrate these valid historical rows.
        assert stored_rows(database) == before_rows
        assert schema(database) == before_schema
        assert not (state / "bootstrap.json").exists()
        assert lifecycle.get("/api/health").status_code == 200
        clients = {}
        for username in ("admin", "bob"):
            client = TestClient(application)
            response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD}, headers={"Origin": "http://testserver"})
            assert response.status_code == 200, response.text
            clients[username] = (client, {"Origin": "http://testserver", "X-CSRF-Token": response.json()["csrf_token"]})
            assert response.json()["user"]["id"] == next(row[0] for row in users if row[1] == username)
        denied = lifecycle.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}, headers={"Origin": "http://testserver"})
        assert denied.status_code == 401
        after_auth = stored_rows(database, sessions=False)
        assert after_auth == {table: rows for table, rows in before_rows.items() if table != "sessions"}
        with sqlite3.connect(database) as conn:
            assert not old_expired_tokens & {row[0] for row in conn.execute("SELECT token_hash FROM sessions")}
        for expected in fixture["responses"] + fixture["details"]:
            client, headers = clients[expected["actor"]]
            response = client.get(expected["path"], headers=headers)
            assert response.status_code == 200, (expected["path"], response.text)
            assert response.json() == expected["json"], expected["path"]
        for expected in fixture["rejections"]:
            client, headers = clients[expected["actor"]]
            response = client.request(expected["method"], expected["path"], json=expected.get("json"), headers=headers)
            assert response.status_code == expected["status"], (expected["path"], response.text)
        for expected in fixture.get("downloads", []):
            client, headers = clients["admin"]
            response = client.get(expected["path"], headers=headers)
            assert response.status_code == 200 and response.content == expected["text"].encode()
        assert stored_rows(database, sessions=False) == after_auth
        assert schema(database) == before_schema
        with sqlite3.connect(database) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("SELECT id,username,password_hash,active FROM users ORDER BY id").fetchall() == users
            if app_id == "files":
                stored = conn.execute("SELECT blob_key,size,deleted_at FROM files WHERE kind='file'").fetchall()
                assert {row[0] for row in stored} == set(fixture["blobs"])
                assert any(row[2] is not None for row in stored) and any(row[2] is None for row in stored)
                for key, size, _ in stored:
                    assert (tmp_path / "blobs" / key).read_bytes() == fixture["blobs"][key].encode()
                    assert size == len(fixture["blobs"][key].encode())
            elif app_id == "projects":
                assert {row[0] for row in conn.execute("SELECT DISTINCT status FROM tasks")} == {item["id"] for item in CATALOG["task_statuses"]}
                assert {row[0] for row in conn.execute("SELECT DISTINCT priority FROM tasks")} == {item["id"] for item in CATALOG["task_priorities"]}
                assert conn.execute("SELECT count(*) FROM tasks WHERE revision=2").fetchone()[0] == 2
                assert conn.execute("SELECT count(*) FROM tasks WHERE assignee_id=2").fetchone()[0] == 9
            elif app_id == "notes":
                assert conn.execute("SELECT archived,revision FROM notes ORDER BY id").fetchall() == [(0, 2), (1, 2)]
            else:
                assert conn.execute("SELECT count(*) FROM messages WHERE deleted=1 AND body=''").fetchone()[0] == 1
                assert [dict(zip(("conversation_id", "user_id", "message_id"), row)) for row in conn.execute("SELECT conversation_id,user_id,message_id FROM read_markers")] == fixture["read_markers"]
