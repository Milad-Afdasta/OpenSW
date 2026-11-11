"""Run the shipped verifier against four real HTTP servers and private data."""

import json
import os
from pathlib import Path
import re
import subprocess
import ssl
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from tools.verify_api import APP_ROUTES, COMMON_ROUTES, Actor, Response, Verifier
from opensw.main import create_app


ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:18184"
PASSWORD = "OpenSW-live-check-admin-9!"


@pytest.mark.parametrize("app_id", tuple(APP_ROUTES))
def test_verifier_route_manifest_covers_the_entire_selected_api(tmp_path, app_id):
    application = create_app(app_id=app_id, data_dir=tmp_path / app_id, config={
        "app": app_id, "instance_name": "manifest", "team_name": "Manifest",
        "public_url": URL, "max_upload_mb": 1,
    })
    actual = set()
    for path, operations in application.openapi()["paths"].items():
        if path.startswith("/api/"):
            normalized = re.sub(r"\{[^}]+\}", "{id}", path)
            actual.update(f"{method.upper()} {normalized}" for method in operations if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"})
    assert COMMON_ROUTES | APP_ROUTES[app_id] == actual


@pytest.mark.parametrize("app_id", ("files", "projects", "notes", "chat"))
@pytest.mark.parametrize("trusted_https", (False, True))
def test_shipped_verifier_exercises_every_route_without_mutating_operator(tmp_path, app_id, trusted_https):
    url = "https://localhost:18185" if trusted_https else URL
    ca_file = None
    ssl_args = []
    context = None
    if trusted_https:
        certificate, key = tmp_path / "fixture-ca.pem", tmp_path / "fixture-key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(key), "-out", str(certificate), "-days", "1", "-subj", "/CN=localhost",
                        "-addext", "subjectAltName=DNS:localhost"], check=True, capture_output=True)
        ca_file = str(certificate)
        context = ssl.create_default_context(cafile=ca_file)
        ssl_args = ["--ssl-keyfile", str(key), "--ssl-certfile", ca_file]
    data = tmp_path / app_id
    state = data / "state"
    state.mkdir(parents=True, mode=0o700)
    (state / "config.json").write_text(json.dumps({
        "app": app_id, "instance_name": "live-check", "team_name": "Live check",
        "public_url": url, "max_upload_mb": 1,
    }))
    (state / "bootstrap.json").write_text(json.dumps({"username": "admin", "password": PASSWORD}))
    (state / "bootstrap.json").chmod(0o600)
    env = os.environ.copy()
    env.update(PYTHONPATH=str(ROOT / "backend"), OPENSW_APP=app_id, OPENSW_DATA_DIR=str(data))
    with (tmp_path / "server.log").open("w+") as log:
        server = subprocess.Popen([
            sys.executable, "-m", "uvicorn", "opensw.main:app",
            "--host", "127.0.0.1", "--port", "18185" if trusted_https else "18184", "--no-access-log", *ssl_args,
        ], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with urlopen(url + "/api/health", timeout=0.5, context=context) as response:
                        if response.status == 200:
                            break
                except (OSError, URLError):
                    pass
                if server.poll() is not None or time.monotonic() >= deadline:
                    log.flush()
                    log.seek(0)
                    pytest.fail("Owned live server did not become ready: " + log.read())
                time.sleep(0.05)
            # The independent operator session predates verification and must
            # stay valid when the verifier logs out its own operator session.
            if trusted_https:
                # The custom CA is explicit. Default trust and wrong-hostname
                # connections both fail before any login/body is submitted.
                with pytest.raises(URLError, match="CERTIFICATE_VERIFY_FAILED"):
                    Actor(url, url, "admin", PASSWORD).request("GET", "/api/health")
                with pytest.raises(URLError, match="CERTIFICATE_VERIFY_FAILED"):
                    Actor("https://127.0.0.1:18185", url, "admin", PASSWORD, ca_file=ca_file).request("GET", "/api/health")
            operator = Actor(url, url, "admin", PASSWORD, ca_file=ca_file)
            login = operator.request("POST", "/api/auth/login", payload={"username": "admin", "password": PASSWORD})
            assert login.status == 200
            assert all(cookie.secure is trusted_https for cookie in operator.cookies)
            session = login.json()
            operator.csrf, operator.user = session["csrf_token"], session["user"]
            if app_id == "files":
                baseline = operator.request("POST", "/api/files/folders", payload={"name": "Existing operator folder", "parent_id": None}).json()
                read_path = f"/api/files/{baseline['id']}"
            elif app_id == "projects":
                baseline = operator.request("POST", "/api/projects", payload={"name": "Existing operator project"}).json()
                read_path = "/api/projects"
            elif app_id == "notes":
                baseline = operator.request("POST", "/api/notes", payload={"title": "Existing operator page", "body": "Never change this content"}).json()
                read_path = f"/api/notes/{baseline['id']}"
            else:
                baseline = operator.request("POST", "/api/chat/channels", payload={"name": "operator-existing"}).json()
                read_path = "/api/chat/channels"
            verifier = Verifier(app_id, url, url, "admin", PASSWORD, ca_file=ca_file)
            assert verifier.run() == 0, verifier.result
            assert verifier.result["routes_exercised"] == verifier.result["routes_required"]
            assert verifier.result["routes_passed"] == verifier.result["routes_required"]
            assert verifier.result["counts"]["FAIL"] == verifier.result["counts"]["BLOCKED"] == 0
            assert operator.request("GET", "/api/me").status == 200
            users = operator.request("GET", "/api/users").json()["items"]
            admin = next(user for user in users if user["id"] == operator.user["id"])
            assert admin["role"] == "admin" and admin["active"] is True
            temporary = [user for user in users if user["id"] in verifier.result["temporary_user_ids"]]
            assert len(temporary) == 2 and all(user["active"] is False for user in temporary)
            retained = operator.request("GET", read_path).json()
            if app_id in {"projects", "chat"}:
                retained = next(row for row in retained["items"] if row["id"] == baseline["id"])
            assert retained == baseline
            new_login = Actor(url, url, "admin", PASSWORD, ca_file=ca_file).request("POST", "/api/auth/login", payload={"username": "admin", "password": PASSWORD})
            assert new_login.status == 200
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


def test_failed_readiness_reports_blocked_routes_and_never_writes(capsys):
    verifier = Verifier("notes", URL, URL, "admin", PASSWORD)
    calls = []

    def reject(method, path, **kwargs):
        calls.append((method, path))
        return Response(503, b'{"detail":"Not initialized"}', {})

    verifier.operator.request = reject
    assert verifier.run() == 1
    assert calls == [("GET", "/api/health")]
    assert verifier.result["counts"]["BLOCKED"] > 0
    assert PASSWORD not in capsys.readouterr().out


def test_connection_failure_does_not_count_blocked_routes_as_exercised():
    verifier = Verifier("files", URL, URL, "admin", PASSWORD)

    def unavailable(method, path, **kwargs):
        raise ConnectionRefusedError("No server is listening")

    verifier.operator.request = unavailable
    assert verifier.run() == 1
    assert verifier.result["routes_exercised"] == verifier.result["routes_passed"] == 0


def test_inside_container_connection_port_matches_docker_server_but_preserves_origin(tmp_path, monkeypatch):
    import io
    from tools import verify_api

    state = tmp_path / "state"
    state.mkdir()
    origin = "http://files-team.localhost:18190"
    (state / "config.json").write_text(json.dumps({"app": "files", "public_url": origin}))
    monkeypatch.setenv("OPENSW_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"username": "admin", "password": PASSWORD})))
    captured = {}

    class CaptureVerifier:
        def __init__(self, app, url, configured_origin, username, password):
            captured.update(app=app, url=url, origin=configured_origin)

        def run(self):
            return 0

    monkeypatch.setattr(verify_api, "Verifier", CaptureVerifier)
    assert verify_api.main(["--inside-container"]) == 0
    docker_cmd = next(line[4:] for line in (ROOT / "Dockerfile").read_text().splitlines() if line.startswith("CMD "))
    command = json.loads(docker_cmd)
    container_port = command[command.index("--port") + 1]
    assert captured["url"] == f"http://127.0.0.1:{container_port}"
    assert captured["origin"] == origin


def test_password_guard_refuses_operator_or_changed_member_identity():
    verifier = Verifier("notes", URL, URL, "admin", PASSWORD)
    verifier.operator.user = {"id": 1, "username": "admin", "role": "admin", "active": True}
    member = {"id": 2, "username": verifier.prefix + "-a", "role": "member", "active": True}
    verifier.users = [member]
    actor = Actor(URL, URL, member["username"], "OpenSW-test-member-password")
    actor.user = dict(member)
    calls = []

    def changed_identity(method, path, **kwargs):
        calls.append((method, path))
        return Response(200, json.dumps({"user": {**member, "role": "admin"}}).encode(), {})

    actor.request = changed_identity
    verifier.password_cycle(actor)
    assert calls == [("GET", "/api/me")]
    assert verifier.steps[-1]["state"] == "BLOCKED"
    actor.user = verifier.operator.user
    calls.clear()
    verifier.password_cycle(actor)
    assert calls == []


def test_deactivation_guard_rechecks_current_member_fields():
    verifier = Verifier("notes", URL, URL, "admin", PASSWORD)
    verifier.operator.user = {"id": 1, "username": "admin", "role": "admin", "active": True}
    verifier.users = [{"id": 2, "username": verifier.prefix + "-a", "role": "member", "active": True}]
    calls = []

    def replaced_user(method, path, **kwargs):
        calls.append((method, path))
        value = {"items": [{"id": 2, "username": "someone-else", "role": "member", "active": True}], "next_cursor": None} if method == "GET" else {"ok": True}
        return Response(200, json.dumps(value).encode(), {})

    verifier.operator.request = replaced_user
    verifier.cleanup()
    assert not any(method == "PATCH" for method, _ in calls)
    assert any(step["state"] == "BLOCKED" for step in verifier.steps)


def test_unconfirmed_write_is_reported_once_and_secrets_are_redacted(capsys):
    verifier = Verifier("notes", URL, URL, "admin", PASSWORD)
    calls = []

    def unknown(method, path, **kwargs):
        calls.append((method, path))
        raise TimeoutError("Connection lost after sending " + PASSWORD)

    verifier.operator.request = unknown
    assert verifier.check("Create unconfirmed fixture", verifier.operator, "POST", "/api/notes", route="POST /api/notes", payload={"title": verifier.prefix}) is None
    assert calls == [("POST", "/api/notes")]
    output = capsys.readouterr().out
    assert "UNCONFIRMED" in output and "[REDACTED]" in output and PASSWORD not in output


def test_public_origin_cannot_be_verified_via_a_cookie_downgrading_http_connection():
    with pytest.raises(ValueError, match="actual HTTPS"):
        Verifier("files", "http://127.0.0.1:8000", "https://files.example.com", "admin", PASSWORD)
