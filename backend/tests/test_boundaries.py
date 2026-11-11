"""Failure-boundary proofs using the production factory and real SQLite."""

from concurrent.futures import ThreadPoolExecutor
import asyncio
import json
from pathlib import Path
import socket
import sqlite3
import stat
import tempfile
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from opensw import auth
from opensw import db as database
from opensw.apps import files
from opensw.main import create_app


PASSWORD = "OpenSW-test-password-9!"


def application(tmp_path: Path, app_id="files"):
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "state/bootstrap.json").write_text(json.dumps({"username": "admin", "password": PASSWORD}))
    config = {"app": app_id, "instance_name": "boundary", "team_name": "Boundary", "public_url": "http://testserver", "max_upload_mb": 1}
    return create_app(app_id, tmp_path, config), config


def sign_in(client, username="admin", password=PASSWORD):
    response = client.post("/api/auth/login", json={"username": username, "password": password}, headers={"Origin": "http://testserver"})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"], "Origin": "http://testserver"}


def test_chunked_multipart_actual_bytes_are_bounded_before_spooling(tmp_path):
    app, _ = application(tmp_path)
    with TestClient(app) as client:
        headers = sign_in(client)
        headers["Content-Type"] = "multipart/form-data; boundary=OpenSW-boundary"

        def chunks():
            yield b'--OpenSW-boundary\r\nContent-Disposition: form-data; name="file"; filename="oversized.bin"\r\nContent-Type: application/octet-stream\r\n\r\n'
            for _ in range(40):
                yield b"x" * (64 * 1024)
            yield b"\r\n--OpenSW-boundary--\r\n"

        result = client.post("/api/files/upload", content=chunks(), headers=headers)
        assert result.status_code == 413
        assert client.get("/api/files").json()["items"] == []
        assert list((tmp_path / "blobs").iterdir()) == []


def test_validation_never_echoes_password_input(tmp_path):
    app, _ = application(tmp_path, "notes")
    with TestClient(app) as client:
        secret = "private-password-that-must-not-appear"
        response = client.post("/api/auth/login", json={"username": "admin", "password": secret, "unexpected": secret})
        assert response.status_code == 422
        assert secret not in response.text
        assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize("invalid_token", [None, b"", b"incorrect-ascii-token", b"\x80", b"\xe9", b"\xff"])
def test_invalid_csrf_header_is_rejected_without_mutation_or_session_loss(tmp_path, invalid_token):
    app, _ = application(tmp_path, "notes")
    with TestClient(app, raise_server_exceptions=False) as client:
        valid_headers = sign_in(client)
        headers = [(b"Origin", b"http://testserver")]
        if invalid_token is not None:
            headers.append((b"X-CSRF-Token", invalid_token))
        for path, payload in [
            ("/api/notes", {"title": "Must not be created"}),
            ("/api/users", {"username": "rejected", "display_name": "Rejected", "password": PASSWORD}),
            ("/api/auth/logout", None),
        ]:
            rejected = client.post(path, json=payload, headers=headers)
            assert rejected.status_code == 403, (path, invalid_token, rejected.text)
            assert client.get("/api/me").status_code == 200
        assert client.get("/api/notes").json()["items"] == []
        assert [user["username"] for user in client.get("/api/users").json()["items"]] == ["admin"]
        assert client.post("/api/notes", json={"title": "Confirmed"}, headers=valid_headers).status_code == 201


def test_valid_large_escaped_unicode_note_and_invalid_surrogate(tmp_path):
    app, _ = application(tmp_path, "notes")
    with TestClient(app) as client:
        headers = sign_in(client)
        body = "\U0001f30d" * 200000
        headers["Content-Type"] = "application/json"
        response = client.post("/api/notes", content=json.dumps({"title": "Unicode", "body": body}), headers=headers)
        assert response.status_code == 201
        assert response.json()["body"] == body
        invalid = client.post("/api/notes", content='{"title":"Invalid","body":"\\ud800"}', headers=headers)
        assert invalid.status_code == 422


def test_disabled_account_cannot_receive_a_late_verified_login(tmp_path, monkeypatch):
    app, _ = application(tmp_path, "notes")
    with TestClient(app) as admin:
        headers = sign_in(admin)
        created = admin.post("/api/users", json={"username": "late-login", "display_name": "Late login", "password": PASSWORD}, headers=headers)
        assert created.status_code == 201
        user_id = created.json()["id"]
        checked, proceed = threading.Event(), threading.Event()
        original_check = auth.check_password

        def delayed_check(password, encoded):
            result = original_check(password, encoded)
            checked.set()
            assert proceed.wait(5)
            return result

        monkeypatch.setattr(auth, "check_password", delayed_check)
        anonymous = TestClient(app)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(anonymous.post, "/api/auth/login", json={"username": "late-login", "password": PASSWORD})
            assert checked.wait(5)
            assert admin.patch(f"/api/users/{user_id}", json={"active": False}, headers=headers).status_code == 200
            proceed.set()
            response = pending.result(timeout=5)
        assert response.status_code == 401
        assert anonymous.get("/api/me").status_code == 401


def test_retained_database_cannot_be_reopened_as_another_instance(tmp_path):
    app, config = application(tmp_path, "notes")
    with TestClient(app) as client:
        headers = sign_in(client)
        note = client.post("/api/notes", json={"title": "Retained", "body": "Do not reset"}, headers=headers).json()
    other = create_app("notes", tmp_path, {**config, "instance_name": "other"})
    with pytest.raises(RuntimeError, match="another app/instance"):
        with TestClient(other):
            pass
    with TestClient(create_app("notes", tmp_path, config)) as client:
        sign_in(client)
        assert client.get(f"/api/notes/{note['id']}").json()["body"] == "Do not reset"


def test_new_database_requires_one_time_bootstrap(tmp_path):
    config = {"app": "chat", "instance_name": "boundary", "team_name": "Boundary", "public_url": "http://testserver", "max_upload_mb": 1}
    with pytest.raises(RuntimeError, match="one-time administrator"):
        with TestClient(create_app("chat", tmp_path, config)):
            pass


def test_nonselected_api_methods_are_all_unavailable(tmp_path):
    app, _ = application(tmp_path, "notes")
    with TestClient(app) as client:
        headers = sign_in(client)
        for method in ("GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"):
            assert client.request(method, "/api/files", headers=headers).status_code == 404


def test_resource_identifiers_reject_values_outside_sqlite_identity_range(tmp_path):
    app, _ = application(tmp_path, "notes")
    with TestClient(app) as client:
        headers = sign_in(client)
        assert client.get(f"/api/notes/{2**80}").status_code == 422
        assert client.get(f"/api/notes?cursor={2**80}").status_code == 422
        assert client.patch("/api/notes/1", json={"expected_revision": 2**80, "title": "Invalid"}, headers=headers).status_code == 422


def test_upload_unknown_commit_acknowledgment_retains_committed_blob(tmp_path, monkeypatch):
    app, _ = application(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        headers = sign_in(client)
        original_connect = sqlite3.connect

        class LostCommitAcknowledgment(sqlite3.Connection):
            def commit(self):
                super().commit()
                raise OSError("The commit acknowledgment was lost after SQLite committed.")

        def connection_with_lost_acknowledgment(*args, **kwargs):
            kwargs["factory"] = LostCommitAcknowledgment
            return original_connect(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(database.sqlite3, "connect", connection_with_lost_acknowledgment)
            response = client.post("/api/files/upload", files={"file": ("committed.txt", b"Retain these committed bytes")}, headers=headers)
        assert response.status_code == 500
        rows = client.get("/api/files").json()["items"]
        assert len(rows) == 1
        assert rows[0]["name"] == "committed.txt"
        assert client.get(f"/api/files/{rows[0]['id']}/download").content == b"Retain these committed bytes"
        assert len(list((tmp_path / "blobs").iterdir())) == 1


def test_upload_syncs_uuid_blob_and_directory_before_metadata_commit(tmp_path, monkeypatch):
    app, _ = application(tmp_path)
    with TestClient(app) as client:
        headers = sign_in(client)
        events = []
        original_connect, original_fsync, original_replace = sqlite3.connect, files.os.fsync, files.os.replace

        def observed_fsync(fd):
            original_fsync(fd)
            events.append("directory-durable" if stat.S_ISDIR(files.os.fstat(fd).st_mode) else "bytes-durable")

        def observed_replace(source, destination):
            assert events == ["bytes-durable"]
            assert len(destination.name) == 32 and all(c in "0123456789abcdef" for c in destination.name)
            original_replace(source, destination)
            events.append("uuid-published")

        class ObservedCommit(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if sql.startswith("INSERT INTO files"):
                    assert events == ["bytes-durable", "uuid-published", "directory-durable"]
                    events.append("metadata-inserted")
                return super().execute(sql, parameters)

            def commit(self):
                assert events[-1] == "metadata-inserted"
                super().commit()
                events.append("metadata-committed")

        def observed_connect(*args, **kwargs):
            kwargs["factory"] = ObservedCommit
            return original_connect(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(database.sqlite3, "connect", observed_connect)
            patch.setattr(files.os, "fsync", observed_fsync)
            patch.setattr(files.os, "replace", observed_replace)
            response = client.post("/api/files/upload", files={"file": ("durable.txt", b"Durable before metadata")}, headers=headers)
        assert response.status_code == 201
        assert events == ["bytes-durable", "uuid-published", "directory-durable", "metadata-inserted", "metadata-committed"]
        assert client.get(f"/api/files/{response.json()['id']}/download").content == b"Durable before metadata"


@pytest.mark.parametrize("failure", ["insert", "commit"])
def test_upload_proven_rollback_removes_only_uncommitted_new_blob(tmp_path, monkeypatch, failure):
    app, _ = application(tmp_path)
    with TestClient(app) as client:
        headers = sign_in(client)
        retained = client.post("/api/files/upload", files={"file": ("retained.txt", b"Existing committed bytes")}, headers=headers).json()
        original_connect = sqlite3.connect

        class RejectedWrite(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if failure == "insert" and sql.startswith("INSERT INTO files"):
                    raise sqlite3.OperationalError("The new metadata was not inserted.")
                return super().execute(sql, parameters)

            def commit(self):
                raise sqlite3.OperationalError("COMMIT was rejected before committing.")

        def rejected_connect(*args, **kwargs):
            kwargs["factory"] = RejectedWrite
            return original_connect(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(database.sqlite3, "connect", rejected_connect)
            response = client.post("/api/files/upload", files={"file": ("rejected.txt", b"Uncommitted bytes")}, headers=headers)
        assert response.status_code == 503
        assert [row["id"] for row in client.get("/api/files").json()["items"]] == [retained["id"]]
        assert client.get(f"/api/files/{retained['id']}/download").content == b"Existing committed bytes"
        assert len(list((tmp_path / "blobs").iterdir())) == 1


def test_upload_cap_accepts_exact_actual_bytes_and_rejects_one_more(tmp_path):
    app, _ = application(tmp_path)
    with TestClient(app) as client:
        headers = sign_in(client)
        payload = b"x" * (1024 * 1024)
        response = client.post("/api/files/upload", files={"file": ("exact.bin", payload)}, headers=headers)
        assert response.status_code == 201 and response.json()["size"] == len(payload)
        assert client.get(f"/api/files/{response.json()['id']}/download").content == payload
        rejected = client.post("/api/files/upload", files={"file": ("excess.bin", payload + b"x")}, headers=headers)
        assert rejected.status_code == 413
        assert len(client.get("/api/files").json()["items"]) == 1
        assert len(list((tmp_path / "blobs").iterdir())) == 1


def test_upload_rechecks_parent_after_streaming_before_metadata_commit(tmp_path, monkeypatch):
    app, _ = application(tmp_path)
    with TestClient(app) as client:
        headers = sign_in(client)
        folder = client.post("/api/files/folders", json={"name": "Destination", "parent_id": None}, headers=headers).json()
        other = TestClient(app)
        other_headers = sign_in(other)
        started, resume = threading.Event(), threading.Event()
        original_read = tempfile.SpooledTemporaryFile.read
        first = True

        def paused_read(upload, size=-1):
            nonlocal first
            if first:
                first = False
                started.set()
                assert resume.wait(5)
            return original_read(upload, size)

        monkeypatch.setattr(tempfile.SpooledTemporaryFile, "read", paused_read)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.post, "/api/files/upload", files={"file": ("late.txt", b"Not committed into a trashed parent")}, data={"parent_id": str(folder["id"])}, headers=headers)
            assert started.wait(5)
            assert other.delete(f"/api/files/{folder['id']}", headers=other_headers).status_code == 200
            resume.set()
            response = pending.result(timeout=5)
        assert response.status_code == 409
        assert client.get("/api/files").json()["items"] == []
        assert list((tmp_path / "blobs").iterdir()) == []


@pytest.mark.parametrize("rejection", ["missing", "invalid", "expired", "revoked", "disabled", "foreign", "missing-csrf", "bad-csrf", "bad-origin"])
def test_upload_auth_admission_rejects_before_any_asgi_body_receive(tmp_path, rejection):
    app, _ = application(tmp_path)
    with TestClient(app) as client:
        signed_headers = sign_in(client)
        cookie_name = app.state.settings.cookie_name
        token = client.cookies.get(cookie_name)
        headers = {"origin": "http://testserver", "x-csrf-token": signed_headers["X-CSRF-Token"]}
        expected = 401
        if rejection == "missing":
            token = None
        elif rejection == "invalid":
            token = "invalid-token"
        elif rejection == "foreign":
            # A real session from another app cannot authenticate to Files even
            # if its cookie is deliberately renamed to the Files cookie name.
            other, _ = application(tmp_path / "foreign", "notes")
            with TestClient(other) as foreign:
                sign_in(foreign)
                token = foreign.cookies.get(other.state.settings.cookie_name)
        elif rejection in {"expired", "revoked", "disabled"}:
            with sqlite3.connect(tmp_path / "opensw.sqlite3") as conn:
                if rejection == "expired":
                    conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00+00:00'")
                elif rejection == "revoked":
                    conn.execute("DELETE FROM sessions")
                else:
                    conn.execute("UPDATE users SET active=0")
        elif rejection == "missing-csrf":
            headers.pop("x-csrf-token")
            expected = 403
        elif rejection == "bad-csrf":
            headers["x-csrf-token"] = "not-the-session-token"
            expected = 403
        else:
            headers["origin"] = "https://another-app.example"
            expected = 403
        if token:
            headers["cookie"] = f"{cookie_name}={token}"
        headers["content-type"] = "multipart/form-data; boundary=upload-admission"
        received, sent = 0, []

        async def receive():
            nonlocal received
            received += 1
            return {"type": "http.request", "body": b"arbitrary large untrusted multipart content", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": "POST", "scheme": "http",
            "path": "/api/files/upload", "raw_path": b"/api/files/upload",
            "root_path": "", "query_string": b"", "client": ("127.0.0.1", 31000),
            "server": ("testserver", 80), "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
        }
        asyncio.run(app(scope, receive, send))
        assert next(message["status"] for message in sent if message["type"] == "http.response.start") == expected
        assert received == 0, "Rejected uploads must not consume or spool any body bytes."
        with sqlite3.connect(tmp_path / "opensw.sqlite3") as conn:
            assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0
        assert list((tmp_path / "blobs").iterdir()) == []


@pytest.mark.parametrize("revoke", ["disable", "logout"])
def test_upload_rechecks_session_under_write_lock_after_body_streaming(tmp_path, monkeypatch, revoke):
    app, _ = application(tmp_path)
    with TestClient(app) as admin:
        admin_headers = sign_in(admin)
        user = admin.post("/api/users", json={"username": "uploader", "display_name": "Uploader", "password": PASSWORD}, headers=admin_headers).json()
        uploader = TestClient(app)
        headers = sign_in(uploader, "uploader")
        started, resume = threading.Event(), threading.Event()
        original_read = tempfile.SpooledTemporaryFile.read

        def paused_read(file, size=-1):
            started.set()
            assert resume.wait(5)
            return original_read(file, size)

        monkeypatch.setattr(tempfile.SpooledTemporaryFile, "read", paused_read)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(uploader.post, "/api/files/upload", files={"file": ("revoked.txt", b"Must not commit")}, headers=headers)
            assert started.wait(5)
            if revoke == "disable":
                assert admin.patch(f"/api/users/{user['id']}", json={"active": False}, headers=admin_headers).status_code == 200
            else:
                assert uploader.post("/api/auth/logout", headers=headers).status_code == 200
            resume.set()
            assert pending.result(timeout=5).status_code == 401
        assert admin.get("/api/files").json()["items"] == []
        assert list((tmp_path / "blobs").iterdir()) == []


def test_real_http_reads_remain_responsive_while_upload_waits_for_sqlite_writer(tmp_path, monkeypatch):
    app, _ = application(tmp_path)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical", access_log=False, proxy_headers=False))
    runner = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    runner.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and runner.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
            headers = sign_in(client)
            attempting = threading.Event()
            original_connect = sqlite3.connect

            class ObservedConnection(sqlite3.Connection):
                def execute(self, sql, parameters=()):
                    if sql == "BEGIN IMMEDIATE":
                        attempting.set()
                    return super().execute(sql, parameters)

            def observed_connect(*args, **kwargs):
                kwargs["factory"] = ObservedConnection
                return original_connect(*args, **kwargs)

            locker = original_connect(tmp_path / "opensw.sqlite3")
            locker.execute("BEGIN IMMEDIATE")
            try:
                with monkeypatch.context() as patch:
                    patch.setattr(database.sqlite3, "connect", observed_connect)
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        pending = executor.submit(client.post, "/api/files/upload", files={"file": ("responsive.txt", b"Confirmed after writer release")}, headers=headers)
                        assert attempting.wait(5)
                        try:
                            for path in ("/api/health", "/api/files"):
                                started = time.monotonic()
                                response = client.get(path, timeout=1)
                                assert response.status_code == 200, response.text
                                assert time.monotonic() - started < 1
                            assert not pending.done(), "The upload must still be waiting on the held write lock."
                        finally:
                            locker.rollback()
                        result = pending.result(timeout=5)
                        assert result.status_code == 201, result.text
                        assert client.get(f"/api/files/{result.json()['id']}/download").content == b"Confirmed after writer release"
            finally:
                locker.close()
    finally:
        server.should_exit = True
        runner.join(timeout=5)
        listener.close()
        assert not runner.is_alive()


def test_rotating_username_and_forwarding_headers_cannot_bypass_peer_pressure(tmp_path, monkeypatch):
    app, _ = application(tmp_path, "notes")
    with TestClient(app) as client:
        calls = []

        def observed_hash(password):
            calls.append(password)
            return "unused"

        monkeypatch.setattr(auth, "hash_password", observed_hash)
        for index in range(auth.LoginAdmission.PEER_LIMIT):
            result = client.post("/api/auth/login", json={"username": f"unknown-{index}", "password": PASSWORD}, headers={"X-Forwarded-For": f"198.51.100.{index}", "Forwarded": f"for=198.51.100.{index}"})
            assert result.status_code == 401
        for index in range(100):
            rejected = client.post("/api/auth/login", json={"username": f"rotated-{index}", "password": PASSWORD}, headers={"X-Forwarded-For": f"203.0.113.{index}"})
            assert rejected.status_code == 429
            assert 1 <= int(rejected.headers["Retry-After"]) <= 60
        assert len(calls) == auth.LoginAdmission.PEER_LIMIT
        limiter = app.state.login_admission
        assert len(limiter.events) == len(limiter.names) == auth.LoginAdmission.PEER_LIMIT
        assert len(limiter.peers) == 1


def test_login_global_and_name_pressure_are_bounded_and_expire(monkeypatch):
    limiter = auth.LoginAdmission()
    current = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: current[0])
    for index in range(limiter.NAME_LIMIT):
        with limiter.attempt(f"peer-{index}", "same-name"):
            pass
    with pytest.raises(auth.HTTPException) as rejected:
        with limiter.attempt("new-peer", "same-name"):
            pytest.fail("Rejected name must not enter hashing.")
    assert rejected.value.status_code == 429 and rejected.value.headers["Retry-After"] == "60"
    for index in range(limiter.GLOBAL_LIMIT - limiter.NAME_LIMIT):
        with limiter.attempt(f"different-peer-{index}", f"name-{index}"):
            pass
    for index in range(1000):
        with pytest.raises(auth.HTTPException) as rejected:
            with limiter.attempt(f"rotating-peer-{index}", f"rotating-name-{index}"):
                pytest.fail("Rejected global pressure must not enter hashing.")
        assert rejected.value.status_code == 429
    assert len(limiter.events) == limiter.GLOBAL_LIMIT
    assert len(limiter.peers) <= limiter.GLOBAL_LIMIT and len(limiter.names) <= limiter.GLOBAL_LIMIT
    current[0] += limiter.WINDOW
    with limiter.attempt("fresh-peer", "fresh-name"):
        pass
    assert list(limiter.events) == [(current[0], "fresh-peer", "fresh-name")]
    assert limiter.peers == {"fresh-peer": 1} and limiter.names == {"fresh-name": 1}


def test_public_password_hash_slots_reject_instead_of_queueing(tmp_path, monkeypatch):
    app, _ = application(tmp_path, "notes")
    with TestClient(app) as client:
        started, release = threading.Barrier(auth.LoginAdmission.HASH_SLOTS + 1), threading.Event()
        active, peak = 0, 0
        lock = threading.Lock()

        def paused_hash(password):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            started.wait(timeout=5)
            assert release.wait(5)
            with lock:
                active -= 1
            return "unused"

        monkeypatch.setattr(auth, "hash_password", paused_hash)
        with ThreadPoolExecutor(max_workers=auth.LoginAdmission.HASH_SLOTS) as executor:
            requests = [executor.submit(client.post, "/api/auth/login", json={"username": f"pending-{index}", "password": PASSWORD}) for index in range(auth.LoginAdmission.HASH_SLOTS)]
            started.wait(timeout=5)
            try:
                rejected = client.post("/api/auth/login", json={"username": "excess", "password": PASSWORD})
                assert rejected.status_code == 429 and rejected.headers["Retry-After"] == "1"
                assert peak == auth.LoginAdmission.HASH_SLOTS
            finally:
                release.set()
            assert [pending.result(timeout=5).status_code for pending in requests] == [401] * auth.LoginAdmission.HASH_SLOTS
