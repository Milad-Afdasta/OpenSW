"""Independent encrypted snapshots. The worker has no Docker access.

The DB snapshot defines the blob set, including Trash. Committed blob bytes are
immutable. Restic executes this module's producer and refuses a snapshot when
that producer fails; there is deliberately no shell pipeline.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit

class BackupError(RuntimeError):
    pass


STOP = threading.Event()
CHILD: subprocess.Popen | None = None
AWS_KEYS = {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_DEFAULT_REGION", "AWS_REGION"}
BLOB = re.compile(r"[0-9a-f]{32}\Z")
SNAPSHOT = re.compile(r"[0-9a-f]{64}\Z")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            os.chmod(name, 0o600)
            json.dump(value, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        if default is not None:
            return default
        raise BackupError(f"Required private file is missing: {path.name}") from None
    except (ValueError, OSError):
        raise BackupError(f"Private state is unreadable: {path.name}") from None


def credentials(data: str) -> dict[str, str]:
    """Literal KEY=value only; never source a shell or evaluate quoted text."""
    result = {}
    for line in data.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key not in AWS_KEYS or key in result or not value or any(ord(c) < 32 for c in value):
            raise BackupError("Storage credentials require unique documented AWS KEY=value entries.")
        result[key] = value
    if not {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"} <= result.keys():
        raise BackupError("Storage credentials need AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.")
    return result


def regular(path: Path):
    """Open without following links, then pin identity for the complete read."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    observed = os.fstat(fd)
    if not stat.S_ISREG(observed.st_mode):
        os.close(fd)
        raise BackupError("Snapshot data must contain regular files only.")
    return os.fdopen(fd, "rb"), observed


def identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def validate_db(path: Path, app: str, instance: str) -> list[tuple[str, int]]:
    with contextlib.closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise BackupError("Snapshot schema version does not match this release.")
        if dict(conn.execute("SELECT key,value FROM metadata")) != {"app": app, "instance_name": instance}:
            raise BackupError("Snapshot database identity does not match this app/instance.")
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or conn.execute("PRAGMA foreign_key_check").fetchall():
            raise BackupError("Snapshot database integrity or foreign keys failed.")
        schema_path = Path(__file__).resolve().parents[1] / "apps" / app / "schema.sql"
        with contextlib.closing(sqlite3.connect(":memory:")) as expected:
            expected.executescript(schema_path.read_text())
            query = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            if conn.execute(query).fetchall() != expected.execute(query).fetchall():
                raise BackupError("Snapshot database schema differs from this source image.")
        if conn.execute("SELECT count(*) FROM users WHERE active=1 AND role='admin'").fetchone()[0] < 1:
            raise BackupError("Snapshot has no active administrator.")
        rows = conn.execute("SELECT blob_key,size FROM files WHERE kind='file' ORDER BY blob_key").fetchall() if app == "files" else []
        if any(not isinstance(key, str) or not BLOB.fullmatch(key) or type(size) is not int or size < 0 for key, size in rows):
            raise BackupError("Snapshot contains an unsafe blob identity or size.")
        return rows


def frozen_database(source: Path, destination: Path, deadline: float) -> None:
    # A URI read-only connection still reads rollback journals; never copy a live
    # SQLite main file or use immutable=1 against the live writer.
    if source.is_symlink() or not source.is_file():
        raise BackupError("The live database must be a regular file.")
    before = source.lstat()
    def progress(*_):
        if STOP.is_set() or time.monotonic() >= deadline:
            raise BackupError("Online SQLite backup exceeded its deadline or was stopped.")
    with contextlib.closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=1)) as live:
        with contextlib.closing(sqlite3.connect(destination)) as frozen:
            live.backup(frozen, pages=128, progress=progress, sleep=0.05)
    progress()
    after = source.lstat()
    if stat.S_ISLNK(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise BackupError("The live database identity changed during capture.")
    os.chmod(destination, 0o600)


class HashingReader:
    def __init__(self, handle, deadline):
        self.handle, self.deadline = handle, deadline
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        if STOP.is_set() or time.monotonic() >= self.deadline:
            raise BackupError("Snapshot stream exceeded its deadline or was stopped.")
        chunk = self.handle.read(size)
        self.digest.update(chunk)
        return chunk


def emit_archive(cfg: dict, output) -> dict:
    from opensw.config import Settings
    data, ops = Path(cfg["data_dir"]), Path(cfg["ops_dir"])
    deadline = time.monotonic() + cfg.get("command_timeout", 600)
    state = data / "state"
    if state.is_symlink() or (data / "blobs").is_symlink():
        raise BackupError("Snapshot directories must not be links.")
    config_handle, config_stat = regular(state / "config.json")
    with config_handle:
        config_bytes = config_handle.read()
    runtime = json.loads(config_bytes)
    Settings.load(cfg["app"], data, runtime)
    if runtime["instance_name"] != cfg["instance_name"]:
        raise BackupError("Snapshot configuration belongs to another instance.")
    manifest = {"format": 1, "app": cfg["app"], "instance_name": cfg["instance_name"],
                "schema_version": 1, "image_id": cfg["image_id"],
                "source_sha256": cfg["source_sha256"], "created_at": time.time(), "files": {}}
    with tempfile.TemporaryDirectory(prefix="capture-", dir=ops) as scratch:
        frozen = Path(scratch) / "opensw.sqlite3"
        frozen_database(data / "opensw.sqlite3", frozen, deadline)
        blobs = validate_db(frozen, cfg["app"], cfg["instance_name"])
        with tarfile.open(fileobj=output, mode="w|", format=tarfile.PAX_FORMAT) as archive:
            def add(name, handle, size):
                member = tarfile.TarInfo(name)
                member.mode, member.size, member.mtime = 0o600, size, 0
                reader = HashingReader(handle, deadline)
                archive.addfile(member, reader)
                manifest["files"][name] = {"size": size, "sha256": reader.digest.hexdigest()}
            with frozen.open("rb") as handle:
                add("opensw.sqlite3", handle, frozen.stat().st_size)
            add("state/config.json", io.BytesIO(config_bytes), len(config_bytes))
            for key, expected_size in blobs:
                path = data / "blobs" / key
                try:
                    handle, before = regular(path)
                    with handle:
                        if before.st_size != expected_size:
                            raise BackupError("A referenced blob has an incorrect byte count.")
                        add("blobs/" + key, handle, expected_size)
                        after = os.fstat(handle.fileno())
                    if identity(before) != identity(after) or identity(path.lstat()) != identity(before):
                        raise BackupError("A referenced immutable blob changed during capture.")
                except OSError:
                    raise BackupError("A referenced blob is missing or unsafe.") from None
            current, current_stat = regular(state / "config.json")
            with current:
                if current.read() != config_bytes or identity(config_stat) != identity(current_stat):
                    raise BackupError("Runtime configuration changed during capture.")
            payload = json.dumps(manifest, sort_keys=True).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mode = len(payload), 0o600
            archive.addfile(info, io.BytesIO(payload))
    return manifest


def restore_archive(archive_path: Path, destination: Path, cfg: dict, *, new_origin=None) -> dict:
    from opensw.config import Settings
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise BackupError("Restore requires a new empty destination directory.")
    with tempfile.TemporaryDirectory(prefix="restore-", dir=cfg["ops_dir"]) as temporary:
        stage = Path(temporary)
        seen, actual = set(), {}
        with tarfile.open(archive_path, "r|*") as archive:
            for member in archive:
                name = member.name
                parts = PurePosixPath(name).parts
                allowed = name in {"opensw.sqlite3", "state/config.json", "manifest.json"} or (len(parts) == 2 and parts[0] == "blobs" and BLOB.fullmatch(parts[1]))
                if not allowed or name in seen or member.type != tarfile.REGTYPE or member.size < 0 or name.startswith("/") or ".." in parts or name != str(PurePosixPath(name)):
                    raise BackupError("Restore archive contains unsafe, duplicate or unexpected entries.")
                seen.add(name)
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                digest = hashlib.sha256()
                count = 0
                with archive.extractfile(member) as source, path.open("xb") as output:
                    os.chmod(path, 0o600)
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
                        digest.update(chunk)
                        count += len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                actual[name] = {"size": count, "sha256": digest.hexdigest()}
        manifest = read_json(stage / "manifest.json")
        if set(manifest) != {"format", "app", "instance_name", "schema_version", "image_id", "source_sha256", "created_at", "files"} or manifest["format"] != 1 or manifest["schema_version"] != 1:
            raise BackupError("Restore manifest format/schema does not match this release.")
        for key in ("app", "instance_name"):
            if manifest[key] != cfg[key]:
                raise BackupError("Restore identity does not match the selected app and instance.")
        # Artifact IDs describe the original capture. A source-only recovery on
        # another architecture has a different valid executable identity. Data
        # compatibility is owned by the exact app/instance/schema/config below.
        if (not isinstance(manifest["image_id"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", manifest["image_id"]) or
            not isinstance(manifest["source_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["source_sha256"])):
            raise BackupError("Restore historical image/source provenance is malformed.")
        actual.pop("manifest.json", None)
        if actual != manifest["files"]:
            raise BackupError("Restore archive checksum manifest does not match its complete contents.")
        runtime = read_json(stage / "state/config.json")
        settings = Settings.load(cfg["app"], stage, runtime)
        if settings.instance_name != cfg["instance_name"]:
            raise BackupError("Restored runtime identity does not match this instance.")
        blobs = validate_db(stage / "opensw.sqlite3", cfg["app"], cfg["instance_name"])
        expected = {"opensw.sqlite3", "state/config.json"} | {"blobs/" + key for key, _ in blobs}
        if set(actual) != expected or any(actual["blobs/" + key]["size"] != size for key, size in blobs):
            raise BackupError("Restore is missing a referenced blob or has unexpected data.")
        if new_origin is not None:
            parsed = urlsplit(new_origin)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise BackupError("An explicit replacement origin must be one HTTPS origin.")
            runtime["public_url"] = new_origin.rstrip("/")
            Settings.load(cfg["app"], stage, runtime)
            atomic_json(stage / "state/config.json", runtime)
        with contextlib.closing(sqlite3.connect(stage / "opensw.sqlite3")) as conn:
            conn.execute("DELETE FROM sessions")
            conn.commit()
        # A partially copied recovery volume is never returned as a valid restore.
        # The original volume is never mounted writable by this path.
        for path in stage.iterdir():
            target = destination / path.name
            if path.is_dir():
                shutil.copytree(path, target)
            else:
                shutil.copyfile(path, target)
                os.chmod(target, 0o600)
        if cfg["app"] == "files":
            (destination / "blobs").mkdir(exist_ok=True, mode=0o700)
        for directory, folders, files in os.walk(destination):
            os.chmod(directory, 0o700)
            for filename in files:
                with (Path(directory) / filename).open("rb") as handle:
                    os.fsync(handle.fileno())
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return manifest


def terminate(*_):
    STOP.set()
    if CHILD is not None and CHILD.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(CHILD.pid, signal.SIGTERM)


class Worker:
    def __init__(self, cfg: dict, config_path: Path):
        self.cfg, self.config_path = cfg, config_path
        self.ops = Path(cfg["ops_dir"])
        self.owner = "opensw:" + cfg["app"] + ":" + cfg["instance_name"]
        self.host = "opensw-" + cfg["app"] + "-" + cfg["instance_name"]

    def capture_identity(self):
        return {key: self.cfg[key] for key in ("app", "instance_name", "image_id", "source_sha256", "configuration_id", "repository")}

    def current_capture(self, value):
        return isinstance(value, dict) and all(key in value and value[key] == expected for key, expected in self.capture_identity().items())

    def current_attempt(self, attempt):
        return (isinstance(attempt, dict) and set(attempt) == {"tag", "at", "identity"}
                and isinstance(attempt["tag"], str) and re.fullmatch(r"run:[0-9a-f]{32}", attempt["tag"])
                and type(attempt["at"]) in {int, float} and math.isfinite(attempt["at"]) and attempt["at"] > 0
                and attempt["identity"] == self.capture_identity())

    def preserve_unresolved(self, state, reason):
        # Only displaced/unconfirmed attempts are retained here, in the same
        # local status file. No old storage credentials are retained or used.
        state.setdefault("unresolved_attempts", []).append({"attempt": state.pop("pending_run"), "reason": reason})

    def verified_success(self, state, snapshot, attempt):
        # Confirmation never changes the original capture's time or identity.
        state.update(status="healthy", phase="idle", last_error=None,
                     last_verified=time.time(), verified_image=attempt["identity"]["image_id"],
                     verified_configuration=attempt["identity"]["configuration_id"],
                     last_success={"snapshot": snapshot, "at": attempt["at"], **attempt["identity"]})

    def reconcile_pending(self, state):
        if "pending_run" not in state:
            return
        attempt = state["pending_run"]
        if not self.current_attempt(attempt):
            self.preserve_unresolved(state, "Capture identity differs from the current configuration or is malformed; publication remains unconfirmed.")
            state.update(status="starting", phase="awaiting-current-capture", needs_capture=True,
                         last_error="An earlier unresolved attempt was preserved. A distinct current capture is required; the old upload was not replayed.")
            return
        # This tag lookup is valid only in the exact capture configuration. A
        # newly configured repository must never be queried for an old run tag.
        rows = self.snapshots()
        matches = [row for row in rows if attempt["tag"] in row.get("tags", [])]
        if len(matches) > 1:
            raise BackupError("A unique run tag has multiple snapshots; repository identity is inconsistent.")
        if not matches:
            self.preserve_unresolved(state, "No confirmed snapshot found during the one read-only reconciliation.")
            state.update(status="failed", last_error="The interrupted upload has no confirmed snapshot. It was not replayed; the next schedule makes a new capture.")
        else:
            snapshot = matches[0]["id"]
            if not isinstance(snapshot, str) or not SNAPSHOT.fullmatch(snapshot):
                raise BackupError("Interrupted upload snapshot identity is malformed; publication remains unconfirmed.")
            self.verify(snapshot, full=True)
            state.pop("pending_run")
            self.verified_success(state, snapshot, attempt)

    def restic(self, *args, check=True, output=None):
        global CHILD
        if STOP.is_set():
            raise BackupError("Backup worker is stopping.")
        env = {key: value for key, value in os.environ.items() if not key.startswith(("AWS_", "RESTIC_"))}
        env.update(credentials(Path(self.cfg["credentials_file"]).read_text()))
        env.update(RESTIC_REPOSITORY=self.cfg["repository"], RESTIC_PASSWORD_FILE=self.cfg["password_file"], RESTIC_CACHE_DIR=str(self.ops / "cache"))
        command = ["restic"]
        if self.cfg.get("ca_file"):
            command += ["--cacert", self.cfg["ca_file"]]
        command += list(args)
        CHILD = subprocess.Popen(command, stdout=output if output is not None else subprocess.PIPE, stderr=subprocess.PIPE, env=env, start_new_session=True)
        child = CHILD
        try:
            try:
                deadline = time.monotonic() + self.cfg.get("command_timeout", 600)
                while True:
                    if STOP.is_set() or time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(command, 0)
                    try:
                        stdout, _ = child.communicate(timeout=min(1, max(0.01, deadline - time.monotonic())))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.communicate()
                raise BackupError("Restic timed out or was stopped; upload outcome is unknown and was not replayed.") from None
            if check and child.returncode:
                # Do not leak storage/provider errors, credentials or URLs into logs.
                raise BackupError(f"Restic {args[0]} failed (exit {child.returncode}); inspect storage access, TLS and repository locks. No upload replay or automatic unlock was attempted.")
            return child.returncode, stdout or b""
        finally:
            CHILD = None

    def snapshots(self, *, initialize=False):
        code, raw = self.restic("snapshots", "--json", check=False)
        if code == 10 and initialize:
            self.restic("init")
            code, raw = self.restic("snapshots", "--json")
        if code:
            raise BackupError("Cannot read the encrypted repository; no initialization, retry or unlock was attempted.")
        rows = json.loads(raw)
        for row in rows:
            if row.get("hostname") != self.host or self.owner not in row.get("tags", []) or row.get("paths") != ["/opensw.tar"]:
                raise BackupError("Repository has foreign snapshots; retention and backup are refused.")
        return rows

    def unlocked(self):
        _, raw = self.restic("list", "locks", "--json")
        # Restic 0.19.1 list emits one hex object ID per line even with --json;
        # no locks produces an empty stream (verified against the pinned image).
        if raw.strip():
            raise BackupError("Repository contains a lock. Confirm its owner/liveness; no automatic unlock was attempted.")

    def verify(self, snapshot: str, *, full: bool):
        self.restic("check")
        if full:
            with tempfile.TemporaryDirectory(prefix="verify-", dir=self.ops) as temporary:
                directory = Path(temporary)
                archive = directory / "opensw.tar"
                with archive.open("xb") as output:
                    self.restic("dump", snapshot, "/opensw.tar", output=output)
                recovered = directory / "data"
                recovered.mkdir(mode=0o700)
                restore_archive(archive, recovered, self.cfg)

    @contextlib.contextmanager
    def lock(self):
        with (self.ops / "run.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BackupError("This app already has a backup/restore operation in progress.") from None
            yield

    def once(self):
        with self.lock():
            state = read_json(self.ops / "status.json", {})
            self.reconcile_pending(state)
            tag = "run:" + uuid.uuid4().hex
            attempt = {"tag": tag, "at": time.time(), "identity": self.capture_identity()}
            state.update(status="running", last_attempt=attempt["at"], last_attempt_config=attempt["identity"]["configuration_id"], needs_capture=False, phase="preparing", last_error=None)
            atomic_json(self.ops / "status.json", state)
            try:
                self.snapshots(initialize=True)
                self.unlocked()
                state.update(pending_run=attempt, phase="uploading")
                atomic_json(self.ops / "status.json", state)
                self.restic("backup", "--json", "--host", self.host, "--tag", self.owner,
                            "--tag", tag, "--stdin-from-command", "--stdin-filename", "opensw.tar", "--",
                            sys.executable, "-m", "operations.backup", "--config", str(self.config_path), "emit")
                rows = self.snapshots()
                matches = [row for row in rows if tag in row.get("tags", [])]
                if len(matches) != 1 or not SNAPSHOT.fullmatch(matches[0].get("id", "")):
                    raise BackupError("Upload completion could not be confirmed by its unique run tag.")
                snapshot = matches[0]["id"]
                state.pop("pending_run", None)
                state.update(phase="verifying", last_success={"snapshot": snapshot, "at": attempt["at"], **attempt["identity"]})
                atomic_json(self.ops / "status.json", state)
                full = not state.get("last_verified") or time.time() - state["last_verified"] >= self.cfg["verify_every_days"] * 86400 or state.get("verified_image") != self.cfg["image_id"] or state.get("verified_configuration") != self.cfg["configuration_id"]
                self.verify(snapshot, full=full)
                if full:
                    state.update(last_verified=time.time(), verified_image=attempt["identity"]["image_id"], verified_configuration=attempt["identity"]["configuration_id"])
                # Check ownership and absence of foreign locks again immediately
                # before the only destructive repository command.
                self.snapshots()
                self.unlocked()
                state["phase"] = "retention"
                atomic_json(self.ops / "status.json", state)
                self.restic("forget", "--host", self.host, "--tag", self.owner, "--group-by", "host,paths",
                            "--keep-within", f"{self.cfg['retain_days']}d", "--keep-last", "1", "--prune")
                state.update(status="healthy", phase="idle", last_error=None)
                atomic_json(self.ops / "status.json", state)
                return state
            except BaseException as exc:
                state.update(status="failed", last_error=str(exc) if isinstance(exc, BackupError) else "Snapshot operation failed; inspect source data and private storage configuration.")
                atomic_json(self.ops / "status.json", state)
                raise

    def reconcile_restart(self):
        """One read-only reconciliation of an interrupted upload; never replay it."""
        with self.lock():
            state = read_json(self.ops / "status.json", {})
            if "pending_run" not in state:
                if state.get("status") == "running":
                    # The worker stopped before upload, or after a confirmed
                    # upload while verifying/pruning. Neither command is replayed.
                    try:
                        success = state.get("last_success", {})
                        if success.get("at", 0) < state.get("last_attempt", 0) or not self.current_capture(success):
                            raise BackupError("The previous attempt stopped before a confirmed upload; no command was replayed.")
                        self.snapshots()
                        self.verify(success["snapshot"], full=True)
                        state.update(status="healthy", phase="idle", last_error=None, last_verified=time.time(), verified_image=success["image_id"], verified_configuration=success["configuration_id"])
                    except (BackupError, OSError, ValueError, sqlite3.Error, tarfile.TarError) as exc:
                        state.update(status="failed", last_error=str(exc) if isinstance(exc, BackupError) else "Interrupted verification failed.")
                    atomic_json(self.ops / "status.json", state)
                return
            try:
                self.reconcile_pending(state)
            except (BackupError, OSError, ValueError, sqlite3.Error, tarfile.TarError) as exc:
                state.update(status="failed", last_error=str(exc) if isinstance(exc, BackupError) else "Interrupted upload reconciliation failed.")
            atomic_json(self.ops / "status.json", state)

    def status(self):
        state = read_json(self.ops / "status.json", {"status": "starting"})
        heartbeat = read_json(self.ops / "heartbeat.json", {})
        state["heartbeat"] = heartbeat.get("at")
        state["healthy"] = False
        if not heartbeat.get("at") or time.time() - heartbeat["at"] > 90:
            state["status"] = "failed"
            state["health_error"] = "Backup worker heartbeat is missing or stale."
        elif state.get("status") in {"healthy", "running"}:
            if (not state.get("last_success") or time.time() - state["last_success"]["at"] > self.cfg["every_hours"] * 3600 + self.cfg.get("command_timeout", 600) or not state.get("last_verified") or time.time() - state["last_verified"] > self.cfg["verify_every_days"] * 86400 + self.cfg["every_hours"] * 3600 + self.cfg.get("command_timeout", 600) or not self.current_capture(state["last_success"]) or state.get("verified_image") != self.cfg["image_id"] or state.get("verified_configuration") != self.cfg["configuration_id"]):
                if state["status"] == "healthy":
                    state["status"] = "failed"
                state["health_error"] = "A successful backup or restore verification is overdue."
            else:
                state["healthy"] = True
        return state

    def worker(self):
        def pulse():
            while not STOP.is_set():
                atomic_json(self.ops / "heartbeat.json", {"at": time.time()})
                STOP.wait(15)
        thread = threading.Thread(target=pulse, daemon=True)
        thread.start()
        try:
            self.reconcile_restart()
            while not STOP.is_set():
                state = read_json(self.ops / "status.json", {})
                due = state.get("last_attempt", 0) + self.cfg["every_hours"] * 3600
                if not state or state.get("needs_capture") or state.get("last_attempt_config") != self.cfg["configuration_id"]:
                    due = 0
                if time.time() >= due:
                    try:
                        self.once()
                    except (BackupError, OSError, ValueError, sqlite3.Error, tarfile.TarError):
                        # Pre-upload reconciliation can fail too. Give it the
                        # same finite schedule instead of hammering storage.
                        with contextlib.suppress(BackupError):
                            with self.lock():
                                failed = read_json(self.ops / "status.json", {})
                                failed.update(status="failed", last_attempt=time.time(), last_attempt_config=self.cfg.get("configuration_id"))
                                if not failed.get("last_error"):
                                    failed["last_error"] = "Repository reconciliation failed; inspect access, ownership and locks. No upload was replayed."
                                atomic_json(self.ops / "status.json", failed)
                STOP.wait(2)
        finally:
            STOP.set()
            thread.join(timeout=2)
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="OpenSW encrypted backup worker")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("command", choices=["worker", "once", "status", "health", "emit", "restore"])
    parser.add_argument("--snapshot")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--new-origin")
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        cfg = read_json(args.config)
        worker = Worker(cfg, args.config)
        if args.command == "emit":
            emit_archive(cfg, sys.stdout.buffer)
            return 0
        if args.command in {"health", "status"}:
            state = worker.status()
            print(json.dumps(state))
            return 0 if state["healthy"] else 1
        if args.command == "worker":
            return worker.worker()
        if args.command == "once":
            print(json.dumps(worker.once()))
            return 0
        if not args.snapshot or not SNAPSHOT.fullmatch(args.snapshot) or not args.destination:
            raise BackupError("Restore requires a complete snapshot ID and empty destination.")
        with worker.lock():
            rows = worker.snapshots()
            if not any(row["id"] == args.snapshot for row in rows):
                raise BackupError("The requested snapshot is not owned by this app/instance.")
            worker.unlocked()
            with tempfile.TemporaryDirectory(prefix="download-", dir=worker.ops) as temporary:
                path = Path(temporary) / "opensw.tar"
                with path.open("xb") as output:
                    worker.restic("dump", args.snapshot, "/opensw.tar", output=output)
                manifest = restore_archive(path, args.destination, cfg, new_origin=args.new_origin)
            print(json.dumps({"status": "restored", "snapshot": args.snapshot, "manifest": manifest}))
        return 0
    except (BackupError, OSError, ValueError, sqlite3.Error, tarfile.TarError) as exc:
        # Emit's error is consumed by Restic, whose producer exit is authoritative.
        print(str(exc) if isinstance(exc, BackupError) else "Backup operation failed; no successful result was recorded.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
