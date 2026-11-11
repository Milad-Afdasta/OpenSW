"""Snapshot consistency, isolation and publication failure invariants."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import threading
import time
from types import SimpleNamespace

import pytest

from conftest import APP_IDS, SignedIn
from operations import backup
from tools import backups


@pytest.fixture(autouse=True)
def clear_stop():
    backup.STOP.clear()
    yield
    backup.STOP.clear()


def settings(tmp_path, data, app):
    ops = tmp_path / (app + "-ops")
    ops.mkdir()
    cfg = {"app": app, "instance_name": "test", "image_id": "sha256:" + "1" * 64,
           "source_sha256": "2" * 64, "ops_dir": str(ops), "data_dir": str(data),
           "repository": "s3:https://storage.test/bucket/opensw-" + app + "-test",
           "configuration_id": "3" * 64,
           "credentials_file": str(ops / "credentials.env"), "password_file": str(ops / "password.txt"),
           "every_hours": 6, "retain_days": 30, "verify_every_days": 7, "command_timeout": 3}
    (ops / "config.json").write_text(json.dumps(cfg))
    (ops / "credentials.env").write_text("AWS_ACCESS_KEY_ID=example\nAWS_SECRET_ACCESS_KEY=private\n")
    (ops / "password.txt").write_text("strong-private-recovery-password\n")
    return cfg


def capture(tmp_path, cfg):
    path = tmp_path / "snapshot.tar"
    with path.open("wb") as output:
        manifest = backup.emit_archive(cfg, output)
    return path, manifest


@pytest.mark.parametrize("app_id", APP_IDS)
def test_all_apps_snapshot_restore_preserves_identity_content_and_revokes_sessions(app_factory, tmp_path, app_id):
    _, client, data = app_factory(app_id)
    signed = SignedIn(client)
    # The app_factory supplies runtime config in memory; deployment owns its file.
    runtime = client.app.state.settings
    (data / "state/config.json").write_text(json.dumps({key: getattr(runtime, key) for key in ("app", "instance_name", "team_name", "public_url", "max_upload_mb")}))
    if app_id == "files":
        result = signed.post("/api/files/upload", files={"file": ("retained-字.txt", "active and Trash 字".encode(), "text/plain")})
        assert result.status_code == 201, result.text
        assert signed.delete("/api/files/" + str(result.json()["id"])).status_code == 200
        # An orphan belongs to no frozen DB row and must not be captured.
        (data / "blobs" / ("f" * 32)).write_bytes(b"not committed")
    elif app_id == "notes":
        assert signed.post("/api/notes", json={"title": "Retained", "body": "字 revision"}).status_code == 201
    elif app_id == "projects":
        assert signed.post("/api/projects", json={"name": "Retained", "description": "字 project"}).status_code == 201
    else:
        assert signed.post("/api/chat/channels", json={"name": "retained"}).status_code == 201
    cfg = settings(tmp_path, data, app_id)
    archive, manifest = capture(tmp_path, cfg)
    restored = tmp_path / "recovered"
    restored.mkdir()
    result = backup.restore_archive(archive, restored, cfg, new_origin="https://recovery.example.test")
    assert result == manifest
    with sqlite3.connect(data / "opensw.sqlite3") as original, sqlite3.connect(restored / "opensw.sqlite3") as recovered:
        assert original.execute("SELECT count(*) FROM sessions").fetchone()[0] > 0
        assert recovered.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert recovered.execute("SELECT * FROM users").fetchall() == original.execute("SELECT * FROM users").fetchall()
        assert recovered.execute("SELECT * FROM metadata").fetchall() == original.execute("SELECT * FROM metadata").fetchall()
        table = {"files": "files", "notes": "notes", "projects": "projects", "chat": "conversations"}[app_id]
        assert recovered.execute(f"SELECT * FROM {table}").fetchall() == original.execute(f"SELECT * FROM {table}").fetchall()
    assert json.loads((restored / "state/config.json").read_text())["public_url"] == "https://recovery.example.test"
    assert json.loads((data / "state/config.json").read_text())["public_url"] == "http://testserver"
    if app_id == "files":
        assert (restored / "blobs").iterdir()
        assert ("blobs/" + "f" * 32) not in manifest["files"]
        for path in (restored / "blobs").iterdir():
            assert path.read_bytes() == (data / "blobs" / path.name).read_bytes()


def file_fixture(app_factory, tmp_path):
    _, client, data = app_factory("files")
    signed = SignedIn(client)
    response = signed.post("/api/files/upload", files={"file": ("test.bin", b"committed bytes", "application/octet-stream")})
    assert response.status_code == 201, response.text
    state = client.app.state.settings
    (data / "state/config.json").write_text(json.dumps({key: getattr(state, key) for key in ("app", "instance_name", "team_name", "public_url", "max_upload_mb")}))
    return settings(tmp_path, data, "files"), next((data / "blobs").iterdir())


@pytest.mark.parametrize("fault", ["missing", "symlink", "size", "config-drift", "blob-drift", "schema"])
def test_missing_unsafe_changed_or_wrong_schema_data_cannot_finish_archive(app_factory, tmp_path, monkeypatch, fault):
    cfg, blob = file_fixture(app_factory, tmp_path)
    if fault == "missing":
        blob.unlink()
    elif fault == "symlink":
        other = tmp_path / "outside"
        other.write_bytes(blob.read_bytes())
        blob.unlink()
        blob.symlink_to(other)
    elif fault == "size":
        blob.write_bytes(b"short")
    elif fault == "schema":
        with sqlite3.connect(Path(cfg["data_dir"]) / "opensw.sqlite3") as conn:
            conn.execute("CREATE TABLE unexpected (id INTEGER)")
    else:
        original = backup.HashingReader.read
        altered = False
        def read(reader, size=-1):
            nonlocal altered
            chunk = original(reader, size)
            if not altered and isinstance(getattr(reader.handle, "name", None), int) and os.fstat(reader.handle.fileno()).st_ino == blob.stat().st_ino:
                altered = True
                if fault == "config-drift":
                    config = Path(cfg["data_dir"]) / "state/config.json"
                    runtime = json.loads(config.read_text())
                    runtime["public_url"] = "https://changed.example.test"
                    config.write_text(json.dumps(runtime))
                else:
                    blob.write_bytes(b"replaced bytes!")
            return chunk
        monkeypatch.setattr(backup.HashingReader, "read", read)
    with pytest.raises((backup.BackupError, OSError)):
        capture(tmp_path, cfg)


def rewrite_archive(path, transform):
    with tarfile.open(path) as archive:
        members = [(member, archive.extractfile(member).read()) for member in archive]
    with tarfile.open(path, "w") as archive:
        for member, data in transform(members):
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data) if member.isfile() else None)


@pytest.mark.parametrize("fault", ["hash", "missing", "malformed-image", "malformed-source", "foreign-app", "foreign-instance", "foreign-schema", "absolute", "parent", "link", "duplicate"])
def test_restore_fails_closed_and_leaves_destination_empty(app_factory, tmp_path, fault):
    cfg, _ = file_fixture(app_factory, tmp_path)
    path, _ = capture(tmp_path, cfg)
    def corrupt(members):
        if fault == "hash":
            members[0] = (members[0][0], b"bad database")
        elif fault == "missing":
            members = [pair for pair in members if not pair[0].name.startswith("blobs/")]
        elif fault in {"malformed-image", "malformed-source", "foreign-app", "foreign-instance", "foreign-schema"}:
            item, payload = members[-1]
            manifest = json.loads(payload)
            key, value = {"malformed-image": ("image_id", "unproven-image"),
                          "malformed-source": ("source_sha256", "unproven-source"),
                          "foreign-app": ("app", "notes"), "foreign-instance": ("instance_name", "other"),
                          "foreign-schema": ("schema_version", 2)}[fault]
            manifest[key] = value
            members[-1] = (item, json.dumps(manifest).encode())
        elif fault == "duplicate":
            members.append(members[0])
        else:
            item = tarfile.TarInfo({"absolute": "/outside", "parent": "../outside", "link": "blobs/" + "a" * 32}[fault])
            if fault == "link":
                item.type = tarfile.SYMTYPE
                item.linkname = "/outside"
            members.append((item, b""))
        return members
    rewrite_archive(path, corrupt)
    destination = tmp_path / "recovered"
    destination.mkdir()
    with pytest.raises(backup.BackupError):
        backup.restore_archive(path, destination, cfg)
    assert list(destination.iterdir()) == []
    assert (Path(cfg["data_dir"]) / "opensw.sqlite3").is_file()


def test_current_source_restores_valid_historical_artifact_ids_and_preserves_provenance(app_factory, tmp_path):
    cfg, blob = file_fixture(app_factory, tmp_path)
    archive, manifest = capture(tmp_path, cfg)
    def historical(members):
        item, payload = members[-1]
        historical_manifest = json.loads(payload)
        historical_manifest.update(image_id="sha256:" + "c" * 64, source_sha256="d" * 64)
        members[-1] = (item, json.dumps(historical_manifest).encode())
        return members
    rewrite_archive(archive, historical)
    destination = tmp_path / "recovered-current-source"
    destination.mkdir()
    result = backup.restore_archive(archive, destination, cfg)
    assert result["image_id"] == "sha256:" + "c" * 64
    assert result["source_sha256"] == "d" * 64
    assert result["files"] == manifest["files"]
    assert (destination / "blobs" / blob.name).read_bytes() == blob.read_bytes()
    assert backup.validate_db(destination / "opensw.sqlite3", cfg["app"], cfg["instance_name"])
    with sqlite3.connect(destination / "opensw.sqlite3") as conn:
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_restore_rejects_nonempty_destination_and_http_origin(app_factory, tmp_path):
    cfg, _ = file_fixture(app_factory, tmp_path)
    path, _ = capture(tmp_path, cfg)
    destination = tmp_path / "recovered"
    destination.mkdir()
    (destination / "original").write_bytes(b"unchanged")
    with pytest.raises(backup.BackupError, match="empty"):
        backup.restore_archive(path, destination, cfg)
    (destination / "original").unlink()
    with pytest.raises(backup.BackupError, match="HTTPS"):
        backup.restore_archive(path, destination, cfg, new_origin="http://insecure.test")
    assert list(destination.iterdir()) == []


def test_online_sqlite_deadline_is_enforced(app_factory, tmp_path):
    _, _, data = app_factory("notes")
    with pytest.raises(backup.BackupError, match="deadline"):
        backup.frozen_database(data / "opensw.sqlite3", tmp_path / "frozen", time.monotonic() - 1)


def worker_cfg(tmp_path):
    cfg = settings(tmp_path, tmp_path, "notes")
    return backup.Worker(cfg, Path(cfg["ops_dir"]) / "config.json")


def owned(worker, tag, snapshot="a" * 64):
    return {"id": snapshot, "hostname": worker.host, "paths": ["/opensw.tar"], "tags": [worker.owner, tag]}


@pytest.mark.parametrize("failure", ["producer", "timeout", "incomplete", "foreign", "lock", "verify"])
def test_failed_backup_never_prunes_or_overwrites_prior_success(tmp_path, monkeypatch, failure):
    worker = worker_cfg(tmp_path)
    prior = {"snapshot": "b" * 64, "at": 123, "image_id": worker.cfg["image_id"]}
    backup.atomic_json(worker.ops / "status.json", {"last_success": prior})
    commands, rows = [], []
    def restic(*args, check=True, output=None):
        commands.append(args)
        if args[0] == "snapshots":
            return 0, json.dumps([{"hostname": "foreign"}] if failure == "foreign" else rows).encode()
        if args[0] == "list":
            return 0, b"f" * 64 + b"\n" if failure == "lock" else b""
        if args[0] == "backup":
            assert "--stdin-from-command" in args
            assert "--stdin-filename" in args and "opensw.tar" in args
            if failure in {"producer", "timeout", "incomplete"}:
                raise backup.BackupError(failure)
            rows.append(owned(worker, args[args.index("--tag", args.index("--tag") + 1) + 1]))
        return 0, b""
    monkeypatch.setattr(worker, "restic", restic)
    monkeypatch.setattr(worker, "verify", lambda *_, **__: (_ for _ in ()).throw(backup.BackupError("verify")))
    with pytest.raises(backup.BackupError):
        worker.once()
    state = backup.read_json(worker.ops / "status.json")
    assert state["status"] == "failed"
    assert bool(state.get("pending_run")) == (failure in {"producer", "timeout", "incomplete"}), "Only an issued upload can have an unknown publication outcome"
    if failure != "verify":
        assert state["last_success"] == prior
    assert not any(command[0] in {"forget", "prune", "unlock"} for command in commands)


def test_success_keeps_stable_retention_groups_and_weekly_full_verification(tmp_path, monkeypatch):
    worker = worker_cfg(tmp_path)
    commands, rows, checks = [], [], []
    def restic(*args, **kwargs):
        commands.append(args)
        if args[0] == "snapshots":
            return 0, json.dumps(rows).encode()
        if args[0] == "list":
            return 0, b""
        if args[0] == "backup":
            rows.append(owned(worker, args[args.index("--tag", args.index("--tag") + 1) + 1]))
        return 0, b""
    monkeypatch.setattr(worker, "restic", restic)
    monkeypatch.setattr(worker, "verify", lambda snapshot, *, full: checks.append(full))
    assert worker.once()["status"] == "healthy"
    assert worker.once()["status"] == "healthy"
    assert checks == [True, False]
    for command in (c for c in commands if c[0] == "forget"):
        assert command[command.index("--group-by") + 1] == "host,paths"
        assert command[command.index("--keep-last") + 1] == "1"
        assert command[command.index("--keep-within") + 1] == "30d"
        assert "tags" not in command
    state = backup.read_json(worker.ops / "status.json")
    state["last_verified"] = time.time() - 8 * 86400
    backup.atomic_json(worker.ops / "status.json", state)
    worker.once()
    assert checks == [True, False, True]


@pytest.mark.parametrize("found", [False, True])
def test_interrupted_upload_is_reconciled_readonly_never_replayed(tmp_path, monkeypatch, found):
    worker = worker_cfg(tmp_path)
    tag = "run:" + "c" * 32
    attempt = {"tag": tag, "at": time.time(), "identity": worker.capture_identity()}
    backup.atomic_json(worker.ops / "status.json", {"status": "running", "last_attempt": attempt["at"], "last_attempt_config": worker.cfg["configuration_id"], "pending_run": attempt})
    commands = []
    def restic(*args, **kwargs):
        commands.append(args)
        assert args[0] == "snapshots"
        return 0, json.dumps([owned(worker, tag)] if found else []).encode()
    monkeypatch.setattr(worker, "restic", restic)
    monkeypatch.setattr(worker, "verify", lambda *_, **__: None)
    worker.reconcile_restart()
    state = backup.read_json(worker.ops / "status.json")
    assert state["status"] == ("healthy" if found else "failed")
    assert "pending_run" not in state
    assert len(commands) == 1
    if found:
        assert state["last_success"] == {"snapshot": "a" * 64, "at": attempt["at"], **attempt["identity"]}
        assert state["verified_configuration"] == attempt["identity"]["configuration_id"]
    else:
        assert state["unresolved_attempts"][0]["attempt"] == attempt


@pytest.mark.parametrize("change", ["image", "repository", "credentials", "password"])
def test_changed_configuration_preserves_old_unknown_attempt_without_query_and_requires_fresh_capture(tmp_path, monkeypatch, change):
    worker = worker_cfg(tmp_path)
    old_identity = worker.capture_identity()
    old_tag = "run:" + "a" * 32
    attempt = {"tag": old_tag, "at": time.time() - 10, "identity": old_identity}
    old_success = {"snapshot": "b" * 64, "at": time.time() - 20, **old_identity}
    backup.atomic_json(worker.ops / "status.json", {"status": "running", "phase": "uploading", "last_attempt": attempt["at"],
                       "last_attempt_config": old_identity["configuration_id"], "pending_run": attempt,
                       "last_success": old_success, "last_verified": time.time(), "verified_image": old_identity["image_id"],
                       "verified_configuration": old_identity["configuration_id"]})
    # The config hash changes for an image, endpoint, credential or password
    # change. Old credentials are unavailable; only current settings exist.
    worker.cfg["configuration_id"] = "4" * 64
    if change == "image":
        worker.cfg.update(image_id="sha256:" + "5" * 64, source_sha256="6" * 64)
    elif change == "repository":
        worker.cfg["repository"] = "s3:https://new-storage.test/bucket/opensw-notes-test"
    elif change == "credentials":
        Path(worker.cfg["credentials_file"]).write_text("AWS_ACCESS_KEY_ID=new\nAWS_SECRET_ACCESS_KEY=new-private-key\n")
    else:
        Path(worker.cfg["password_file"]).write_text("new-private-recovery-password")
    rows, calls, full_checks, snapshots_during_old_attempt = [], [], [], []
    def restic(*args, **_):
        calls.append(args)
        state = backup.read_json(worker.ops / "status.json")
        if args[0] == "snapshots":
            if state.get("pending_run") == attempt:
                snapshots_during_old_attempt.append(args)
                raise backup.BackupError("Old storage is unavailable and cannot be queried with current credentials")
            return 0, json.dumps(rows).encode()
        if args[0] == "list":
            return 0, b""
        if args[0] == "backup":
            new_tag = args[args.index("--tag", args.index("--tag") + 1) + 1]
            assert new_tag != old_tag
            assert state["pending_run"]["identity"] == worker.capture_identity()
            rows.append(owned(worker, new_tag))
        return 0, b""
    monkeypatch.setattr(worker, "restic", restic)
    monkeypatch.setattr(worker, "verify", lambda snapshot, *, full: full_checks.append((snapshot, full)))
    backup.atomic_json(worker.ops / "heartbeat.json", {"at": time.time()})
    worker.reconcile_restart()
    current = worker.status()
    assert not current["healthy"]
    assert current["status"] == "starting"
    assert current["needs_capture"]
    assert current["last_success"] == old_success
    assert current["unresolved_attempts"][0]["attempt"] == attempt
    assert calls == [], "A changed repository/configuration must not be queried for the old run tag"
    result = worker.once()
    assert result["status"] == "healthy"
    assert result["last_success"] == {"snapshot": "a" * 64, "at": result["last_attempt"], **worker.capture_identity()}
    assert result["unresolved_attempts"][0]["attempt"] == attempt
    assert full_checks == [("a" * 64, True)], "First current-config snapshot requires full restore validation even for the same image"
    assert not snapshots_during_old_attempt
    assert len([call for call in calls if call[0] == "backup"]) == 1
    assert worker.status()["healthy"]


@pytest.mark.parametrize("pending", [None, "", {}, [], "run:" + "a" * 32, {"tag": "run:" + "a" * 32}, {"tag": "invalid", "at": 1, "identity": {}}, {"tag": "run:" + "a" * 32, "at": float("inf"), "identity": {}}])
def test_malformed_retained_attempt_is_preserved_without_identity_fallback_or_storage_query(tmp_path, monkeypatch, pending):
    worker = worker_cfg(tmp_path)
    backup.atomic_json(worker.ops / "status.json", {"status": "running", "last_attempt": time.time(), "last_attempt_config": worker.cfg["configuration_id"], "pending_run": pending})
    monkeypatch.setattr(worker, "restic", lambda *_, **__: pytest.fail("Malformed provenance cannot authorize a repository query"))
    worker.reconcile_restart()
    state = backup.read_json(worker.ops / "status.json")
    assert state["status"] == "starting" and state["needs_capture"]
    assert state["unresolved_attempts"][0]["attempt"] == pending
    assert "last_success" not in state and "last_verified" not in state
    assert "pending_run" not in state


def test_current_identity_cannot_relabel_a_confirmed_old_snapshot_after_verification_restart(tmp_path, monkeypatch):
    worker = worker_cfg(tmp_path)
    old_identity = worker.capture_identity()
    old_success = {"snapshot": "a" * 64, "at": time.time(), **old_identity}
    backup.atomic_json(worker.ops / "status.json", {"status": "running", "phase": "verifying", "last_attempt": old_success["at"],
                       "last_attempt_config": old_identity["configuration_id"], "last_success": old_success})
    worker.cfg["configuration_id"] = "4" * 64
    monkeypatch.setattr(worker, "restic", lambda *_, **__: pytest.fail("Old confirmed capture must not be verified against changed current storage"))
    worker.reconcile_restart()
    state = backup.read_json(worker.ops / "status.json")
    assert state["status"] == "failed"
    assert state["last_success"] == old_success
    assert "last_verified" not in state


def test_current_configuration_health_requires_its_own_full_restore_verification(tmp_path):
    worker = worker_cfg(tmp_path)
    identity = worker.capture_identity()
    backup.atomic_json(worker.ops / "heartbeat.json", {"at": time.time()})
    backup.atomic_json(worker.ops / "status.json", {"status": "healthy", "last_success": {"snapshot": "a" * 64, "at": time.time(), **identity},
                       "last_verified": time.time(), "verified_image": identity["image_id"], "verified_configuration": "older-configuration"})
    assert not worker.status()["healthy"]


def test_restart_scheduler_automatically_makes_one_distinct_current_capture_after_displacing_old_attempt(tmp_path, monkeypatch):
    worker = worker_cfg(tmp_path)
    old_identity = worker.capture_identity()
    old = {"tag": "run:" + "a" * 32, "at": time.time(), "identity": old_identity}
    worker.cfg["configuration_id"] = "4" * 64
    worker.cfg["repository"] = "s3:https://new-storage.test/new-bucket/opensw-notes-test"
    # Even a retained current attempt timestamp cannot postpone the required
    # first current capture until the six-hour schedule.
    backup.atomic_json(worker.ops / "status.json", {"status": "running", "pending_run": old,
                       "last_attempt": time.time(), "last_attempt_config": worker.cfg["configuration_id"]})
    calls, rows, results, errors = [], [], [], []
    def restic(*args, **_):
        calls.append(args)
        if args[0] == "snapshots":
            state = backup.read_json(worker.ops / "status.json")
            assert state.get("pending_run") != old, "Current storage was queried for old unknown publication"
            return 0, json.dumps(rows).encode()
        if args[0] == "list":
            return 0, b""
        if args[0] == "backup":
            tag = args[args.index("--tag", args.index("--tag") + 1) + 1]
            assert tag != old["tag"]
            rows.append(owned(worker, tag))
        if args[0] == "forget":
            backup.STOP.set()
        return 0, b""
    monkeypatch.setattr(worker, "restic", restic)
    monkeypatch.setattr(worker, "verify", lambda *_, **kwargs: None)
    def run():
        try:
            results.append(worker.worker())
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        thread.join(timeout=3)
        assert not thread.is_alive(), "Restart did not promptly finish the required distinct current capture"
    finally:
        backup.STOP.set()
        thread.join(timeout=3)
    assert not errors and results == [0]
    assert len([call for call in calls if call[0] == "backup"]) == 1
    state = backup.read_json(worker.ops / "status.json")
    assert state["status"] == "healthy"
    assert state["last_success"]["configuration_id"] == worker.cfg["configuration_id"]
    assert state["unresolved_attempts"][0]["attempt"] == old


def test_exclusive_run_lock_prevents_schedule_manual_restore_overlap(tmp_path):
    first = worker_cfg(tmp_path)
    second = backup.Worker(first.cfg, first.config_path)
    with first.lock():
        with pytest.raises(backup.BackupError, match="in progress"):
            with second.lock():
                pytest.fail("Overlapping operation obtained the owner lock")


def test_restart_before_upload_fails_without_creating_false_unknown_upload_or_replaying(tmp_path, monkeypatch):
    worker = worker_cfg(tmp_path)
    backup.atomic_json(worker.ops / "status.json", {"status": "running", "phase": "preparing", "last_attempt": time.time()})
    monkeypatch.setattr(worker, "restic", lambda *_, **__: pytest.fail("Preparation interruption must not issue repository commands"))
    worker.reconcile_restart()
    state = backup.read_json(worker.ops / "status.json")
    assert state["status"] == "failed"
    assert "pending_run" not in state


def test_health_requires_heartbeat_success_and_restore_verification(tmp_path):
    worker = worker_cfg(tmp_path)
    state = {"status": "healthy", "last_success": {"at": time.time(), **worker.capture_identity()}, "last_verified": time.time(), "verified_image": worker.cfg["image_id"], "verified_configuration": worker.cfg["configuration_id"]}
    backup.atomic_json(worker.ops / "status.json", state)
    assert worker.status()["status"] == "failed"
    backup.atomic_json(worker.ops / "heartbeat.json", {"at": time.time()})
    assert worker.status()["status"] == "healthy"
    assert worker.status()["healthy"]
    state["status"] = "running"
    backup.atomic_json(worker.ops / "status.json", state)
    assert worker.status()["healthy"], "A fresh verified backup remains healthy during its next capture"
    state["last_verified"] = time.time() - 9 * 86400
    backup.atomic_json(worker.ops / "status.json", state)
    assert not worker.status()["healthy"]
    state["status"] = "healthy"
    backup.atomic_json(worker.ops / "status.json", state)
    assert worker.status()["status"] == "failed"
    state.update(status="failed", last_verified=time.time(), last_error="Latest backup failed")
    backup.atomic_json(worker.ops / "status.json", state)
    assert worker.status()["status"] == "failed"


@pytest.mark.parametrize("text", ["export AWS_ACCESS_KEY_ID=x\nAWS_SECRET_ACCESS_KEY=y", "AWS_ACCESS_KEY_ID=x\nUNSAFE=value\nAWS_SECRET_ACCESS_KEY=y", "AWS_ACCESS_KEY_ID=x\nAWS_ACCESS_KEY_ID=y\nAWS_SECRET_ACCESS_KEY=z", "AWS_ACCESS_KEY_ID=x"])
def test_credentials_reject_shell_format_unknown_duplicate_and_missing_keys(text):
    with pytest.raises(backup.BackupError):
        backup.credentials(text)


def test_credentials_values_are_literal_data_not_shell_code():
    result = backup.credentials("AWS_ACCESS_KEY_ID=$(touch /tmp/no)\nAWS_SECRET_ACCESS_KEY=a=b\n")
    assert result == {"AWS_ACCESS_KEY_ID": "$(touch /tmp/no)", "AWS_SECRET_ACCESS_KEY": "a=b"}


def test_restic_timeout_terminates_its_process_group_and_leaves_outcome_unknown(tmp_path, monkeypatch):
    worker = worker_cfg(tmp_path)
    worker.cfg["command_timeout"] = 2
    pidfile = tmp_path / "child.pid"
    worker.cfg["repository"] = str(pidfile)
    executable = tmp_path / "restic"
    executable.write_text(f"#!{sys.executable}\nimport os,subprocess,sys,time\nfrom pathlib import Path\np = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\nPath(os.environ['RESTIC_REPOSITORY']).write_text(str(p.pid))\ntime.sleep(30)\n")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    with pytest.raises(backup.BackupError, match="unknown.*not replayed"):
        worker.restic("backup")
    assert backup.CHILD is None
    pid = pidfile.read_text()
    observed = subprocess.run(["ps", "-p", pid, "-o", "stat="], capture_output=True, text=True)
    assert not observed.stdout.strip() or observed.stdout.strip().startswith("Z"), "A producer descendant survived the bounded stop"


def test_controller_refuses_active_original_or_foreign_restore_volume_before_mutation(tmp_path):
    class Target:
        config = type("Config", (), {"volume": "original", "deployment": "opensw-notes-test"})()
        def volume_info(self, name):
            return {"Name": name}
        def container(self, name):
            return {"Id": "active-app"}
        def require_owned(self, resource):
            raise RuntimeError("foreign existing volume")
        def docker(self, *args, **kwargs):
            pytest.fail("Restore mutated Docker before rejecting its destination")
    target = Target()
    for destination in ("original", "opensw-notes-test-backup-ops", "existing-recovery"):
        with pytest.raises(Exception, match="separate|already owns|foreign"):
            backups.restore_to_empty(target, "a" * 64, destination, "sha256:" + "1" * 64)


def test_controller_restores_on_empty_host_without_worker_or_prior_ops_and_never_schedules_upload(tmp_path):
    password, keys = tmp_path / "recovery.txt", tmp_path / "storage.env"
    password.write_text("private-recovery-password")
    keys.write_text("AWS_ACCESS_KEY_ID=key\nAWS_SECRET_ACCESS_KEY=secret\n")
    password.chmod(0o600)
    keys.chmod(0o600)
    image = "sha256:" + "1" * 64
    class Target:
        config = SimpleNamespace(app="notes", instance_name="test", deployment="opensw-notes-test", volume="opensw-notes-test-data",
                                 labels={"io.opensw.managed": "true", "io.opensw.app": "notes", "io.opensw.instance": "test"},
                                 backups=SimpleNamespace(repository="s3:https://storage.test/bucket", password_file=str(password), credentials_file=str(keys), ca_file="", every_hours=6, retain_days=30, verify_every_days=7))
        def __init__(self):
            self.volumes, self.commands = {}, []
        def volume_info(self, name):
            return self.volumes.get(name)
        def container(self, name):
            return None
        def require_owned(self, resource, **_):
            assert resource["Labels"] == self.config.labels
        def docker(self, *args, **kwargs):
            self.commands.append((args, kwargs))
            if args[:2] == ("image", "inspect"):
                return SimpleNamespace(stdout=json.dumps([{"Id": image, "Config": {"Labels": {"io.opensw.app": "notes", "io.opensw.source-sha256": "2" * 64}}}]).encode(), returncode=0)
            if args[:2] == ("volume", "create"):
                self.volumes[args[-1]] = {"Labels": self.config.labels}
            if "restore" in args:
                return SimpleNamespace(stdout=b'{"status":"restored"}', returncode=0)
            return SimpleNamespace(stdout=b"", returncode=0)
    target = Target()
    assert backups.restore_to_empty(target, "a" * 64, image_id=image)["status"] == "restored"
    assert set(target.volumes) == {"opensw-notes-test-backup-ops", "opensw-notes-test-data"}
    restore_commands = [command for command, _ in target.commands if "restore" in command]
    assert len(restore_commands) == 1
    assert all("worker" not in command and "once" not in command for command, _ in target.commands)
    assert all("/var/run/docker.sock" not in " ".join(command) for command, _ in target.commands)
    assert "type=volume,source=opensw-notes-test-data,target=/restore" in restore_commands[0]
