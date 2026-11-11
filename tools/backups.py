"""Docker controller for one app's independent backup worker.

Only this controller sees Docker. Worker storage keys are delivered over stdin
into a private named volume, including when Docker uses a remote SSH engine.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import time

from operations.backup import BackupError, credentials
from tools.errors import SetupError


def _install():
    from tools import install
    return install


def _error(message):
    return SetupError(message)


def _name(target):
    return target.config.deployment + "-backup"


def _volume(target):
    return target.config.deployment + "-backup-ops"


def _private(path: str) -> bytes:
    item = Path(path).expanduser()
    if item.is_symlink() or not item.is_file() or item.stat().st_mode & 0o077:
        raise _error("Backup secret files must be regular private files (chmod 600).")
    return item.read_bytes()


def _image(target, image_id):
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise _error("Backups require the selected immutable source image ID.")
    image = json.loads(target.docker("image", "inspect", image_id).stdout)[0]
    labels = image.get("Config", {}).get("Labels", {})
    source = labels.get("io.opensw.source-sha256", "")
    if image["Id"] != image_id or labels.get("io.opensw.app") != target.config.app or not re.fullmatch(r"[0-9a-f]{64}", source):
        raise _error("Backup source-image provenance does not match this app.")
    return source


def _archive(values: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, payload in values.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(payload), 0o600
            archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


def _owned_worker(target):
    worker = target.container(_name(target))
    if worker:
        target.require_owned(worker, container=True)
        mounts = worker.get("Mounts", [])
        if (worker.get("Config", {}).get("User") != "10001:10001" or
            not any(m.get("Name") == target.config.volume and m.get("Destination") == "/data" and not m.get("RW") for m in mounts) or
            not any(m.get("Name") == _volume(target) and m.get("Destination") == "/ops" and m.get("RW") for m in mounts) or
            any(m.get("Destination") == "/var/run/docker.sock" or m.get("Type") == "bind" for m in mounts)):
            raise _error("The named backup worker has unexpected mounts or UID; no changes were made.")
    return worker


def stop(target):
    """Stop its process tree before the app image/origin can change."""
    worker = _owned_worker(target)
    if not worker:
        return None
    if worker["State"].get("Running"):
        target.docker("stop", "--time", "35", _name(target), timeout=45)
    observed = _owned_worker(target)
    if not observed or observed["State"].get("Running"):
        raise _error("Backup worker stop could not be confirmed. App transition is blocked.")
    if observed["State"].get("ExitCode") in {137, 143} or observed["State"].get("OOMKilled"):
        raise _error("Backup worker was forcibly terminated. Its upload outcome is unconfirmed; inspect status/storage locks before an app transition.")
    return observed


def _settings(target, image_id):
    cfg, backup = target.config, target.config.backups
    if backup is None:
        raise _error("Configure [backups] with authorized HTTPS object storage first.")
    password, credential_data = _private(backup.password_file), _private(backup.credentials_file)
    try:
        credentials(credential_data.decode())
        if not password.strip():
            raise ValueError()
    except (BackupError, ValueError, UnicodeError):
        raise _error("Private backup credentials must use unique AWS KEY=value lines with access/secret keys; the recovery password must not be empty.") from None
    source = _image(target, image_id)
    settings = {"app": cfg.app, "instance_name": cfg.instance_name,
                "repository": backup.repository.rstrip("/") + "/" + cfg.deployment,
                "credentials_file": "/ops/credentials.env", "password_file": "/ops/password.txt",
                "ca_file": "/ops/ca.pem" if backup.ca_file else "",
                "every_hours": backup.every_hours, "retain_days": backup.retain_days,
                "verify_every_days": backup.verify_every_days,
                "data_dir": "/data", "ops_dir": "/ops", "image_id": image_id,
                "source_sha256": source, "command_timeout": 600}
    values = {"config.json": json.dumps(settings, sort_keys=True).encode(),
              "credentials.env": credential_data, "password.txt": password}
    if backup.ca_file:
        path = Path(backup.ca_file)
        if path.is_symlink() or not path.is_file():
            raise _error("Backup CA must be a regular trusted certificate file.")
        values["ca.pem"] = path.read_bytes()
    # Configuration identity lives only in private operational state. It makes
    # credential/repository/image changes require a new capture, while restarting
    # an unchanged worker preserves its schedule and verified verdict.
    digest = hashlib.sha256()
    for name in sorted(values):
        digest.update(name.encode() + b"\0" + values[name] + b"\0")
    settings["configuration_id"] = digest.hexdigest()
    values["config.json"] = json.dumps(settings, sort_keys=True).encode()
    return settings, values


def _prepare_ops(target, values):
    retained = target.volume_info(_volume(target))
    if retained:
        target.require_owned(retained)
    if not retained:
        command = ["volume", "create"]
        for key, value in target.config.labels.items():
            command += ["--label", f"{key}={value}"]
        target.docker(*command, _volume(target))
        target.require_owned(target.volume_info(_volume(target)))
    # A stopped worker cannot observe partial secret/config replacement. The app
    # volume is not mounted in this root helper at all.
    target.docker("run", "--rm", "-i", "--network", "none", "--mount",
                  f"type=volume,source={_volume(target)},target=/ops", _install().HELPER,
                  "sh", "-ec", "chmod 700 /ops; tar -xf - -C /ops; chown -R 10001:10001 /ops; chmod 600 /ops/config.json /ops/credentials.env /ops/password.txt",
                  payload=_archive(values))


def configure(target, image_id: str, wait_timeout=600):
    settings, values = _settings(target, image_id)
    existing = _owned_worker(target)
    if existing:
        stop(target)
        target.docker("rm", _name(target))
    _prepare_ops(target, values)
    command = ["run", "--detach", "--name", _name(target), "--restart", "unless-stopped",
               "--user", "10001:10001", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
               "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
               "--mount", f"type=volume,source={target.config.volume},target=/data,readonly",
               "--mount", f"type=volume,source={_volume(target)},target=/ops",
               "--health-cmd", "python -m operations.backup --config /ops/config.json health",
               "--health-interval", "30s", "--health-timeout", "5s", "--health-start-period", "30s", "--health-retries", "2"]
    for key, value in target.config.labels.items():
        command += ["--label", f"{key}={value}"]
    command += [image_id, "python", "-m", "operations.backup", "--config", "/ops/config.json", "worker"]
    target.docker(*command)
    deadline = time.monotonic() + wait_timeout
    while time.monotonic() < deadline:
        observed = status(target)
        if observed["status"] == "healthy":
            return observed
        if observed.get("status") == "failed" and observed.get("last_attempt") and observed.get("last_attempt_config") == settings["configuration_id"]:
            raise _error("The first encrypted backup/restore verification failed. " + observed.get("last_error", observed.get("health_error", "Inspect backup status.")))
        worker = _owned_worker(target)
        if not worker or not worker["State"].get("Running"):
            raise _error("Backup worker stopped before its first verified snapshot. Keep its private operations volume and inspect status.")
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    raise _error("The first verified encrypted backup did not complete within the wait window. No second upload was issued; inspect backup status.")


def status(target):
    worker = _owned_worker(target)
    if not worker:
        return {"status": "not-configured"}
    if worker["State"].get("Running"):
        result = target.docker("exec", _name(target), "python", "-m", "operations.backup", "--config", "/ops/config.json", "status", check=False)
        try:
            value = json.loads(result.stdout)
            if isinstance(value, dict) and "status" in value:
                return value
        except ValueError:
            pass
        raise _error("Backup worker status is unreadable; no healthy verdict is available.")
    result = target.docker("run", "--rm", "--network", "none", "--read-only", "--user", "10001:10001", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--mount",
                           f"type=volume,source={_volume(target)},target=/ops,readonly", _install().HELPER,
                           "cat", "/ops/status.json", check=False)
    try:
        value = json.loads(result.stdout)
    except ValueError:
        value = {}
    value.update(status="stopped", worker_exit=worker["State"].get("ExitCode"))
    return value


def run_once(target, timeout=600):
    worker = _owned_worker(target)
    if not worker or not worker["State"].get("Running"):
        raise _error("Start/configure the independent backup worker before requesting a manual snapshot.")
    # This exec shares the worker's flock and persistent state. It never bypasses
    # an active scheduled run and never retries a command with unknown outcome.
    result = target.docker("exec", _name(target), "python", "-m", "operations.backup", "--config", "/ops/config.json", "once", timeout=timeout, check=False)
    if result.returncode:
        raise _error("Manual backup did not pass. Inspect backup status; no automatic retry was issued.")
    return json.loads(result.stdout)


def restore_to_empty(target, snapshot: str, destination_volume: str | None = None, image_id: str | None = None, new_origin=None, timeout=600):
    destination_volume = destination_volume or target.config.volume
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", destination_volume) or destination_volume == _volume(target):
        raise _error("Restore requires a complete snapshot ID and a separate empty data volume.")
    if destination_volume == target.config.volume and target.container(target.config.deployment):
        raise _error("An app container already owns the requested destination. Use a new recovery host or separate empty volume.")
    retained = target.volume_info(destination_volume)
    if retained:
        target.require_owned(retained)
    settings, values = _settings(target, image_id)
    stop(target)  # Optional on a fresh recovery host; never starts a scheduler.
    _prepare_ops(target, values)
    if not retained:
        command = ["volume", "create"]
        for key, value in target.config.labels.items():
            command += ["--label", f"{key}={value}"]
        target.docker(*command, destination_volume)
        target.require_owned(target.volume_info(destination_volume))
    target.docker("run", "--rm", "--network", "none", "--mount", f"type=volume,source={destination_volume},target=/restore", _install().HELPER,
                  "sh", "-ec", "test -z \"$(ls -A /restore)\"; chown 10001:10001 /restore; chmod 700 /restore")
    command = ["run", "--rm", "--user", "10001:10001", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--mount", f"type=volume,source={_volume(target)},target=/ops",
               "--mount", f"type=volume,source={destination_volume},target=/restore",
               image_id, "python", "-m", "operations.backup", "--config", "/ops/config.json", "restore", "--snapshot", snapshot, "--destination", "/restore"]
    if new_origin:
        command += ["--new-origin", new_origin]
    result = target.docker(*command, timeout=timeout, check=False)
    if result.returncode:
        raise _error("Restore verification failed. The separate destination was retained for diagnosis and must not be used; the original data was untouched.")
    return json.loads(result.stdout)
