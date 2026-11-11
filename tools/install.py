"""One guided source-build installer; no third-party Python dependencies."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import getpass
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.errors import SetupError
CATALOG = json.loads((ROOT / "shared/catalog.json").read_text())
APPS = {app["id"]: app for app in CATALOG["apps"]}
HELPER = "busybox:1.37.0@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec"
MANAGED = "io.opensw.managed"
IGNORED = {".git", ".venv", "node_modules", "dist", "__pycache__", ".pytest_cache", "test-results", "playwright-report", "artifacts", "data", "secrets"}
DOCKER_SERVER_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?")


def run(command: list[str], *, env: dict[str, str] | None = None,
        payload: bytes | None = None, timeout: int = 60,
        check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, input=payload, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, timeout=timeout)
    except FileNotFoundError as exc:
        raise SetupError(f"Required program is missing: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SetupError("The command timed out; its outcome may be unknown. No retry was issued. "
                         "Use the status/logs commands to inspect this installation before continuing.") from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).decode(errors="replace")[-5000:]
        raise SetupError(f"Command failed ({result.returncode}): {shlex.join(command[:4])}\n{detail}")
    return result


def pause_and_check(message: str, check, *, interactive: bool) -> None:
    """One guided prerequisite pause, followed by a real condition check."""
    try:
        check()
        return
    except SetupError as first:
        if not interactive:
            raise SetupError(f"{message}\n{first}") from first
        print(f"\n{message}\nCurrent check: {first}")
        input("Press Enter when you have completed that step (Ctrl+C cancels): ")
    check()


def text(value, field: str, *, minimum=0, maximum=255) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum or any(ord(c) < 32 for c in value):
        raise SetupError(f"'{field}' must be text between {minimum} and {maximum} characters.")
    return value


def integer(value, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise SetupError(f"'{field}' must be a whole number between {minimum} and {maximum}.")
    return value


@dataclasses.dataclass(frozen=True)
class HTTPS:
    domain: str
    email: str
    acme_server: str = "https://acme-v02.api.letsencrypt.org/directory"
    ca_file: str = ""
    http_port: int = 80
    port: int = 443
    dns_resolvers: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Backups:
    repository: str
    credentials_file: str
    password_file: str
    every_hours: int = 6
    retain_days: int = 30
    ca_file: str = ""
    verify_every_days: int = 7


@dataclasses.dataclass(frozen=True)
class Config:
    app: str
    team_name: str
    run_on: str
    server_address: str
    server_username: str
    server_key_file: str
    instance_name: str
    port: int
    ssh_port: int
    max_upload_mb: int
    https: HTTPS | None = None
    backups: Backups | None = None

    @property
    def deployment(self) -> str:
        return f"opensw-{self.app}-{self.instance_name}"

    @property
    def volume(self) -> str:
        return f"{self.deployment}-data"

    @property
    def public_url(self) -> str:
        if self.https:
            suffix = "" if self.https.port == 443 else f":{self.https.port}"
            return f"https://{self.https.domain}{suffix}"
        return f"http://{self.app}-{self.instance_name}.localhost:{self.port}"

    @property
    def runtime(self) -> dict:
        return {"app": self.app, "instance_name": self.instance_name,
                "team_name": self.team_name, "public_url": self.public_url,
                "max_upload_mb": self.max_upload_mb}

    @property
    def labels(self) -> dict[str, str]:
        return {MANAGED: "true", "io.opensw.app": self.app,
                "io.opensw.instance": self.instance_name}


def input_file(value, field: str, config_path: Path, *, private=False, optional=False) -> str:
    value = text(value, field, maximum=4096)
    if optional and not value:
        return ""
    result = Path(value).expanduser()
    if not result.is_absolute():
        result = config_path.resolve().parent / result
    try:
        if not result.is_file() or result.is_symlink():
            raise SetupError(f"'{field}' must name an existing regular file, without a symlink.")
        if private and result.stat().st_mode & 0o077:
            raise SetupError(f"'{field}' must be private: chmod 600 {shlex.quote(str(result))}.")
    except OSError as exc:
        raise SetupError(f"Cannot inspect '{field}': {exc}") from exc
    return str(result.resolve())


def section(raw, name, allowed):
    value = raw.get(name)
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - allowed:
        raise SetupError(f"[{name}] has unknown options or is not a configuration section.")
    return value


def https_address(value: str, field: str):
    try:
        endpoint = urlsplit(value)
        endpoint.port
    except ValueError as exc:
        raise SetupError(f"'{field}' contains an invalid HTTPS address or port.") from exc
    if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.fragment:
        raise SetupError(f"'{field}' must be an HTTPS address without embedded credentials or a fragment.")
    return endpoint


def load_config(path: Path) -> Config:
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SetupError(f"Cannot read configuration {path}: {exc}") from exc
    allowed = {"app", "team_name", "run_on", "server_address", "server_username", "server_key_file", "advanced", "https", "backups"}
    unknown = set(raw) - allowed
    if unknown:
        raise SetupError(f"Unknown configuration option(s): {', '.join(sorted(unknown))}. Check the commented example.")
    app = text(raw.get("app", ""), "app")
    if app not in APPS:
        raise SetupError(f"Choose one app: {', '.join(APPS)}.")
    mode = text(raw.get("run_on", "this-computer"), "run_on")
    if mode not in {"this-computer", "my-server"}:
        raise SetupError("'run_on' must be 'this-computer' or 'my-server'.")
    advanced = raw.get("advanced", {})
    if not isinstance(advanced, dict):
        raise SetupError("[advanced] must be an optional configuration section.")
    advanced_allowed = {"instance_name", "port", "ssh_port"}
    if app == "files":
        advanced_allowed.add("max_upload_mb")
    unknown = set(advanced) - advanced_allowed
    if unknown:
        raise SetupError(f"Unknown advanced option(s) for {app}: {', '.join(sorted(unknown))}.")
    instance = text(advanced.get("instance_name", "team"), "advanced.instance_name", minimum=1, maximum=32)
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", instance):
        raise SetupError("'advanced.instance_name' uses lowercase letters, numbers, and internal hyphens only.")
    address = text(raw.get("server_address", ""), "server_address")
    username = text(raw.get("server_username", ""), "server_username", maximum=64)
    key = text(raw.get("server_key_file", ""), "server_key_file", maximum=4096)
    if mode == "my-server":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", address):
            raise SetupError("Enter your server's IP address, hostname, or SSH alias (without https://). IPv6 literals are not supported yet.")
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,63}", username):
            raise SetupError("Enter your server login name, for example ubuntu or root.")
        if key:
            key_path = Path(key).expanduser()
            if not key_path.is_absolute():
                key_path = path.resolve().parent / key_path
            if not key_path.is_file():
                raise SetupError(f"Your SSH key file was not found: {key_path}")
            key = str(key_path.resolve())
    elif address or username or key:
        raise SetupError("Server details are filled in, but run_on is 'this-computer'. Choose 'my-server', or clear the three server fields.")
    https_raw = section(raw, "https", {field.name for field in dataclasses.fields(HTTPS)})
    https = None
    if https_raw is not None:
        domain = text(https_raw.get("domain", ""), "https.domain", minimum=1, maximum=253)
        if domain != domain.lower() or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", domain):
            raise SetupError("'https.domain' must be one lowercase DNS hostname or canonical public IPv4 address, without a wildcard, scheme or port.")
        email = text(https_raw.get("email", ""), "https.email", minimum=3)
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise SetupError("'https.email' must be a valid certificate contact email address.")
        acme = text(https_raw.get("acme_server", HTTPS.acme_server), "https.acme_server", maximum=2048)
        https_address(acme, "https.acme_server")
        resolvers = https_raw.get("dns_resolvers", [])
        try:
            if not isinstance(resolvers, list) or any(not isinstance(item, str) for item in resolvers):
                raise ValueError()
            for item in resolvers:
                ipaddress.ip_address(item)
        except ValueError:
            raise SetupError("'https.dns_resolvers' must be a list of DNS server IP addresses using standard port 53.") from None
        https = HTTPS(domain, email, acme, input_file(https_raw.get("ca_file", ""), "https.ca_file", path, optional=True),
                      integer(https_raw.get("http_port", 80), "https.http_port", 1, 65535),
                      integer(https_raw.get("port", 443), "https.port", 1, 65535), tuple(resolvers))
        if https.http_port == https.port:
            raise SetupError("HTTPS and HTTP challenge ports must differ.")
    backup_raw = section(raw, "backups", {field.name for field in dataclasses.fields(Backups)})
    backups = None
    if backup_raw is not None:
        repository = text(backup_raw.get("repository", ""), "backups.repository", minimum=1, maximum=2048).rstrip("/")
        endpoint = https_address(repository[3:] if repository.startswith("s3:") else "", "backups.repository")
        if not endpoint.path.strip("/") or endpoint.query:
            raise SetupError("'backups.repository' must be s3:https://STORAGE-HOST/BUCKET[/PREFIX], without embedded credentials.")
        backups = Backups(repository,
            input_file(backup_raw.get("credentials_file", ""), "backups.credentials_file", path, private=True),
            input_file(backup_raw.get("password_file", ""), "backups.password_file", path, private=True),
            integer(backup_raw.get("every_hours", 6), "backups.every_hours", 1, 168),
            integer(backup_raw.get("retain_days", 30), "backups.retain_days", 1, 36500),
            input_file(backup_raw.get("ca_file", ""), "backups.ca_file", path, optional=True),
            integer(backup_raw.get("verify_every_days", 7), "backups.verify_every_days", 1, 365))
    if https and not backups:
        raise SetupError("Public HTTPS requires [backups] with encrypted off-host S3 storage. Configure an authorized bucket and private credential/password files first.")
    return Config(app, text(raw.get("team_name", "My team"), "team_name", minimum=1, maximum=80),
                  mode, address, username, key, instance,
                  integer(advanced.get("port", APPS[app]["port"]), "advanced.port", 1024, 65535),
                  integer(advanced.get("ssh_port", 22), "advanced.ssh_port", 1, 65535),
                  integer(advanced.get("max_upload_mb", 25), "advanced.max_upload_mb", 1, 1024), https, backups)


def wizard() -> Path:
    if not sys.stdin.isatty():
        raise SetupError("Choose an example with --config apps/files/config.example.toml (or projects, notes, chat). Run without --config in a terminal for the guided setup.")
    print("\nOpenSW — your software, your server.\n")
    choices = list(APPS)
    for index, app in enumerate(choices, 1):
        print(f"  {index}. {APPS[app]['name']}")
    choice = input("Which app? [1]: ").strip() or "1"
    if choice not in {"1", "2", "3", "4"}:
        raise SetupError("Choose an app number from 1 to 4 and run setup again.")
    app = choices[int(choice) - 1]
    print("\n  1. This computer\n  2. An existing server")
    target = input("Where should it run? [1]: ").strip() or "1"
    if target not in {"1", "2"}:
        raise SetupError("Choose 1 for this computer or 2 for your server.")
    values = {"app": app, "team_name": input("Team name [My team]: ").strip() or "My team",
              "run_on": "this-computer" if target == "1" else "my-server",
              "server_address": "", "server_username": "", "server_key_file": ""}
    if target == "2":
        values["server_address"] = input("Server IP address or hostname: ").strip()
        values["server_username"] = input("Server login name (for example ubuntu): ").strip()
        values["server_key_file"] = input("Path to your SSH key file (blank uses your SSH agent): ").strip()
    path = ROOT / f"{app}.local.toml"
    if path.exists():
        print(f"\nYour saved configuration already exists: {path}\nUsing that file without overwriting it.")
        return path
    production = input("Publish with automatic HTTPS and encrypted off-host backups? [y/N]: ").strip().lower() == "y"
    tls_values, backup_values = {}, {}
    if production:
        tls_values = {"domain": input("App's DNS hostname or public IPv4 address: ").strip(),
                      "email": input("Certificate contact email: ").strip()}
        print("Use an existing authorized S3-compatible HTTPS bucket. OpenSW will not create a paid storage account.")
        repository = input("Storage address (s3:https://storage.example.com/bucket): ").strip()
        access = getpass.getpass("Storage access key (hidden): ").strip()
        secret = getpass.getpass("Storage secret key (hidden): ").strip()
        recovery = getpass.getpass("Choose a backup recovery password (keep a separate safe copy): ")
        if not access or not secret or len(recovery) < 12 or "\n" in access + secret + recovery:
            raise SetupError("Storage keys must be nonempty single-line values; choose a recovery password of at least 12 characters.")
        if getpass.getpass("Enter the recovery password again: ") != recovery:
            raise SetupError("Recovery passwords did not match.")
        secrets_directory = ROOT / "secrets"
        if secrets_directory.is_symlink():
            raise SetupError("The private secrets directory must not be a symlink.")
        secrets_directory.mkdir(mode=0o700, exist_ok=True)
        if secrets_directory.stat().st_mode & 0o077:
            raise SetupError("Keep the secrets directory private: chmod 700 " + shlex.quote(str(secrets_directory)))
        credentials_path = secrets_directory / f"{app}-storage-credentials.env"
        recovery_path = secrets_directory / f"{app}-backup-password.txt"
        for filename, content in ((credentials_path, f"AWS_ACCESS_KEY_ID={access}\nAWS_SECRET_ACCESS_KEY={secret}\n"), (recovery_path, recovery + "\n")):
            try:
                secret_fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError as exc:
                raise SetupError(f"Private storage file already exists: {filename}. Review it and configure the saved TOML; it was not overwritten.") from exc
            with os.fdopen(secret_fd, "w") as secret_file:
                secret_file.write(content)
                secret_file.flush()
                os.fsync(secret_file.fileno())
        del access, secret, recovery
        backup_values = {"repository": repository, "credentials_file": str(credentials_path), "password_file": str(recovery_path)}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("# Your private OpenSW configuration. Edit this file to change setup choices.\n")
        for key, value in values.items():
            handle.write(f"{key} = {json.dumps(value, ensure_ascii=False)}\n")
        for name, options in (("https", tls_values), ("backups", backup_values)):
            if options:
                handle.write(f"\n[{name}]\n")
                for key, value in options.items():
                    handle.write(f"{key} = {json.dumps(value, ensure_ascii=False)}\n")
        handle.write("\n# Optional advanced settings are explained in your app's config.example.toml.\n")
    print(f"\nSaved {path}. You can review or edit it before continuing.")
    input("Press Enter to continue: ")
    return path


class DockerTarget:
    def __init__(self, config: Config):
        self.config = config
        self.env = dict(os.environ)
        for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
            self.env.pop(key, None)
        self.temp: tempfile.TemporaryDirectory | None = None
        self.prefix: list[str] = ["docker"]
        self.server_version: str | None = None

    def __enter__(self):
        if not shutil.which("docker"):
            raise SetupError("Install and start Docker: https://docs.docker.com/get-started/get-docker/ . On macOS or Windows, open Docker Desktop and wait until its engine is running.")
        cfg = self.config
        if cfg.run_on == "this-computer":
            context = run(["docker", "context", "show"], env=self.env).stdout.decode().strip()
            settings = json.loads(run(["docker", "context", "inspect", context], env=self.env).stdout)
            endpoint = settings[0]["Endpoints"]["docker"]["Host"]
            if not endpoint.startswith("unix://"):
                raise SetupError("Your selected Docker context is remote. Select your local Docker Desktop/default context, or choose run_on='my-server'. OpenSW will not silently install on that remote target.")
            self.prefix += ["--context", context]
        else:
            ssh = shutil.which("ssh")
            if not ssh:
                raise SetupError("Install an OpenSSH client, then return to setup.")
            self.temp = tempfile.TemporaryDirectory(prefix="opensw-ssh-")
            wrapper = Path(self.temp.name) / "ssh"
            args = [ssh, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes"]
            if cfg.server_key_file:
                args += ["-i", cfg.server_key_file, "-o", "IdentitiesOnly=yes"]
            wrapper.write_text("#!/bin/sh\nexec " + shlex.join(args) + ' "$@"\n')
            wrapper.chmod(0o700)
            self.env["PATH"] = self.temp.name + os.pathsep + self.env.get("PATH", "")
            self.prefix += ["--host", f"ssh://{cfg.server_username}@{cfg.server_address}:{cfg.ssh_port}"]
        return self

    def __exit__(self, *_):
        if self.temp:
            self.temp.cleanup()

    def docker(self, *args: str, payload: bytes | None = None, timeout=60, check=True):
        return run(self.prefix + list(args), env=self.env, payload=payload, timeout=timeout, check=check)

    def preflight(self) -> str:
        cfg = self.config
        if cfg.run_on == "my-server":
            run(["ssh", "-p", str(cfg.ssh_port), f"{cfg.server_username}@{cfg.server_address}", "true"], env=self.env, timeout=40)
        info = json.loads(self.docker("info", "--format", "{{json .}}").stdout)
        if info.get("OSType") != "linux":
            raise SetupError("OpenSW needs Docker's Linux-container engine. Switch Docker Desktop to Linux containers.")
        if not info.get("ID"):
            raise SetupError("Docker did not return an engine identity. Installation stopped before creating resources.")
        version = info.get("ServerVersion")
        parsed = DOCKER_SERVER_VERSION.fullmatch(version) if isinstance(version, str) else None
        if parsed is None or int(parsed.group(1)) < 28:
            raise SetupError("The target Docker daemon must report a stable ServerVersion of 28.0.0 or newer. "
                             "Update Docker on the selected target without deleting its existing volumes, then recheck. "
                             "Use a currently patched Docker release; this minimum is the localhost-publication floor, not a full host-security certification. "
                             "No managed resources were changed.")
        self.server_version = version
        return info["ID"]

    def container(self, name: str) -> dict | None:
        names = self.docker("container", "ls", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}").stdout.decode().splitlines()
        if name not in names:
            return None
        return json.loads(self.docker("container", "inspect", name).stdout)[0]

    def volume_info(self, name: str) -> dict | None:
        names = self.docker("volume", "ls", "--filter", f"name={name}", "--format", "{{.Name}}").stdout.decode().splitlines()
        if name not in names:
            return None
        return json.loads(self.docker("volume", "inspect", name).stdout)[0]

    def require_owned(self, resource: dict, *, container=False):
        labels = resource.get("Config", {}).get("Labels", {}) if container else resource.get("Labels", {})
        if any(labels.get(key) != value for key, value in self.config.labels.items()):
            raise SetupError("A resource with this installation's name already exists without matching OpenSW ownership. Choose another advanced.instance_name. Nothing was adopted or deleted.")

    def helper(self, script: str, *, payload: bytes | None = None, readonly=True) -> bytes:
        mount = f"type=volume,source={self.config.volume},target=/data" + (",readonly" if readonly else "")
        command = ["run", "--rm", "-i", "--network", "none", "--mount", mount]
        if readonly:
            command += ["--read-only", "--user", "10001:10001", "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
        return self.docker(*command, HELPER, "sh", "-ec", script, payload=payload).stdout

    def stored_runtime(self, *, allow_origin_change=False) -> dict:
        try:
            result = json.loads(self.helper("cat /data/state/config.json"))
        except (SetupError, ValueError) as exc:
            raise SetupError("This retained data volume has missing or unreadable installation state. OpenSW will not reseed users or replace credentials. Keep the volume and inspect it before attempting recovery.") from exc
        expected = self.config.runtime
        matches = result == expected or (allow_origin_change and set(result) == set(expected) and
                  {k: v for k, v in result.items() if k != "public_url"} == {k: v for k, v in expected.items() if k != "public_url"})
        if not matches:
            raise SetupError("This installation already exists with a different configuration. Keep its original config, or use a different advanced.instance_name and port for a separate installation. Existing data was preserved.")
        return result

    @contextlib.contextmanager
    def lock(self):
        name = self.config.deployment + "-install-lock"
        token = secrets.token_hex(16)
        args = ["create", "--name", name]
        for key, value in self.config.labels.items():
            args += ["--label", f"{key}={value}"]
        args += ["--label", f"io.opensw.run={token}", HELPER, "true"]
        identifier = None
        try:
            try:
                identifier = self.docker(*args).stdout.decode().strip()
            except SetupError as exc:
                raise SetupError(f"Another installation or a retained lock owns {name}. No automatic unlock was attempted. Inspect it; remove only a confirmed stale OpenSW lock with: {shlex.join(self.prefix + ['container', 'rm', name])}\n{exc}") from exc
            yield
        finally:
            if identifier:
                try:
                    observed = self.container(name)
                    if observed and observed["Id"] == identifier and observed["Config"].get("Labels", {}).get("io.opensw.run") == token:
                        self.docker("container", "rm", identifier)
                except SetupError as exc:
                    print(f"Cleanup notice: installer lock could not be released. {exc}", file=sys.stderr)


def source_archive(*, excluded_paths: set[Path] | None = None) -> tuple[bytes, str]:
    # The caller owns the actual credential/config inputs. Their identity, not
    # a guessed extension, determines what must never leave the controller.
    excluded = {path.expanduser().resolve() for path in (excluded_paths or set())}
    excluded_inodes = set()
    for path in excluded:
        try:
            stat = path.stat()
            excluded_inodes.add((stat.st_dev, stat.st_ino))
        except FileNotFoundError:
            pass
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        for top in ("Dockerfile", ".dockerignore", "backend", "web", "shared", "apps", "operations", "tools/verify_api.py"):
            path = ROOT / top
            if not path.exists():
                raise SetupError(f"Source checkout is incomplete: missing {top}.")
            entries = [path] if path.is_file() else []
            if path.is_dir():
                for directory, folders, files in os.walk(path, followlinks=False):
                    folders[:] = sorted(folder for folder in folders if folder not in IGNORED)
                    entries.extend(Path(directory) / name for name in sorted(files))
            for item in entries:
                relative = item.relative_to(ROOT)
                if not item.is_file() or any(part in IGNORED for part in relative.parts):
                    continue
                stat = item.stat()
                if item.resolve() in excluded or (stat.st_dev, stat.st_ino) in excluded_inodes:
                    continue
                if item.is_symlink():
                    raise SetupError(f"Source context contains a symlink: {relative}. Use an unmodified source checkout.")
                if item.name.endswith((".local.toml", ".pyc", ".log", ".sqlite3", ".pem", ".key")) or item.name.startswith(".env") or item.name in {"config.toml", ".npmrc", ".pypirc", "pip.conf"}:
                    continue
                info = archive.gettarinfo(str(item), arcname=str(relative))
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                with item.open("rb") as handle:
                    archive.addfile(info, handle)
    data = payload.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def bootstrap_archive(cfg: Config, username: str, password: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, content in (("config.json", cfg.runtime), ("bootstrap.json", {"username": username, "password": password})):
            data = json.dumps(content).encode()
            info = tarfile.TarInfo(name)
            info.mode = 0o600
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def get_password(path: str | None, *, interactive: bool, confirm=True,
                 prompt="Choose the administrator password (at least 12 characters): ") -> str:
    if path:
        secret_path = Path(path).expanduser()
        try:
            if secret_path.stat().st_mode & 0o077:
                raise SetupError("Your password file must be private. Run chmod 600 on that file and try again.")
            password = secret_path.read_text().rstrip("\r\n")
        except OSError as exc:
            raise SetupError(f"Cannot read the private password file: {exc}") from exc
    elif interactive:
        password = getpass.getpass(prompt)
        if confirm and getpass.getpass("Enter it again: ") != password:
            raise SetupError("The passwords did not match. Nothing was installed.")
    else:
        raise SetupError("A first installation needs an administrator password. Use a terminal for the hidden prompt, or --password-file /path/to/a/private/file.")
    if not 12 <= len(password) <= 256:
        raise SetupError("The administrator password must contain 12 to 256 characters.")
    return password


def validate_container(target: DockerTarget, container: dict, *, allow_network_transition=False):
    target.require_owned(container, container=True)
    cfg = target.config
    mounts = container.get("Mounts", [])
    if not any(m.get("Name") == cfg.volume and m.get("Destination") == "/data" and m.get("RW") for m in mounts):
        raise SetupError("The existing container does not mount this installation's owned data volume. No changes were made.")
    bindings = container.get("HostConfig", {}).get("PortBindings", {}).get("8000/tcp", [])
    if bindings != [{"HostIp": "127.0.0.1", "HostPort": str(cfg.port)}]:
        raise SetupError("The existing container's network binding differs from the configuration. No changes were made.")
    if cfg.https:
        # An explicit upgrade may still inspect the old private container; its
        # network is established only after the maintained transition starts.
        networks = container.get("NetworkSettings", {}).get("Networks", {})
        if not allow_network_transition and set(networks) != {cfg.deployment + "-private"}:
            raise SetupError("The public app is attached to an unexpected network.")


HEALTH_CODE = "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read().decode())"


def wait_ready(target: DockerTarget, *, timeout: int, name=None):
    deadline = time.monotonic() + timeout
    last = "The application is starting."
    delay = 2
    while time.monotonic() < deadline:
        container = target.container(name or target.config.deployment)
        if not container:
            raise SetupError("The owned application container disappeared during startup. Data was retained; inspect status before retrying.")
        state = container["State"]
        if state.get("Status") in {"dead", "exited"}:
            raise SetupError(f"The application stopped before readiness (exit {state.get('ExitCode')}). Run ./install.sh logs --config YOUR_CONFIG to inspect the cause. Data and credentials were retained.")
        if state.get("Running"):
            result = target.docker("exec", name or target.config.deployment, "python", "-c", HEALTH_CODE, check=False, timeout=20)
            if result.returncode == 0:
                try:
                    body = json.loads(result.stdout)
                    if body.get("status") == "ok" and body.get("app") == target.config.app and body.get("version") == CATALOG["version"] and body.get("schema_version") == 1 and state.get("Health", {}).get("Status") == "healthy":
                        return body
                    last = "Health response or container health does not yet match the selected app/version/schema."
                except ValueError:
                    last = "The application did not return a valid readiness response."
            else:
                last = result.stderr.decode(errors="replace")[-400:]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(delay, remaining))
        delay = min(delay + 1, 5)
    raise SetupError(f"Readiness did not pass within {timeout} seconds. {last}\nNo second installation was attempted; data was retained. Run status and logs with the same --config.")


def check_certificate(gateway, args, *, maintenance=False):
    """One guided DNS/port/CA pause; only the actual TLS check is repeated."""
    pause_and_check(
        "Point this app's DNS A/AAAA records at the configured Docker host, allow its HTTP/HTTPS challenge ports, "
        "and check the explicitly trusted CA if one is configured. The retained application and maintenance routes are preserved.",
        lambda: gateway.check_tls(maintenance=maintenance, timeout=args.wait_timeout),
        interactive=not args.non_interactive and sys.stdin.isatty(),
    )


def tunnel_command(cfg: Config) -> list[str]:
    args = ["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-p", str(cfg.ssh_port)]
    if cfg.server_key_file:
        args += ["-i", cfg.server_key_file, "-o", "IdentitiesOnly=yes"]
    return args + ["-L", f"127.0.0.1:{cfg.port}:127.0.0.1:{cfg.port}", f"{cfg.server_username}@{cfg.server_address}"]


def private_inputs(cfg: Config, args) -> set[Path]:
    paths = {args.config.resolve()}
    for value in (cfg.server_key_file, getattr(args, "password_file", None)):
        if value:
            paths.add(Path(value).expanduser().resolve())
    if cfg.https and cfg.https.ca_file:
        paths.add(Path(cfg.https.ca_file))
    if cfg.backups:
        paths.update(Path(value) for value in (cfg.backups.credentials_file, cfg.backups.password_file, cfg.backups.ca_file) if value)
    return paths


def build_image(target, args):
    cfg = target.config
    archive, digest = source_archive(excluded_paths=private_inputs(cfg, args))
    image = f"opensw-{cfg.app}:source-{digest[:16]}"
    revision = "source-archive:" + digest
    if (ROOT / ".git").is_dir():
        head = run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=False)
        dirty = run(["git", "-C", str(ROOT), "status", "--porcelain"], check=False)
        if not head.returncode and not dirty.returncode and not dirty.stdout.strip():
            revision = head.stdout.decode().strip()
    print("Building the selected app from locked public source…", flush=True)
    target.docker("build", "--build-arg", f"OPENSW_APP={cfg.app}", "--build-arg", f"SOURCE_COMMIT={revision}",
                  "--build-arg", f"SOURCE_SHA256={digest}", "--tag", image, "-", payload=archive, timeout=1200)
    details = json.loads(target.docker("image", "inspect", image).stdout)[0]
    if details.get("Config", {}).get("Labels", {}).get("io.opensw.app") != cfg.app:
        raise SetupError("The built image does not declare the selected app. It was not started.")
    return details["Id"], digest


def image_version(target, image):
    code = "import json; print(json.load(open('/app/shared/catalog.json'))['version'])"
    return target.docker("run", "--rm", "--network", "none", "--read-only", "--entrypoint", "python", image, "-c", code).stdout.decode().strip()


def start_app(target, image, digest, *, network=None, volume=None, name=None, port=None):
    cfg = target.config
    command = ["run", "--detach", "--name", name or cfg.deployment, "--restart", "unless-stopped",
               "--mount", f"type=volume,source={volume or cfg.volume},target=/data",
               "--publish", f"127.0.0.1:{port or cfg.port}:8000", "--security-opt", "no-new-privileges",
               "--cap-drop", "ALL", "--env", "OPENSW_DATA_DIR=/data"]
    if network:
        command += ["--network", network]
    for key, value in cfg.labels.items():
        command += ["--label", f"{key}={value}"]
    command += ["--label", f"io.opensw.source-sha256={digest}", image]
    target.docker(*command)


def transition_runtime(target, image, runtime):
    # App/worker must be confirmed stopped. Session COMMIT precedes config.
    script = r'''
import json,os,sqlite3,sys,tempfile
runtime=json.load(sys.stdin)
root='/data/state'
database='/data/opensw.sqlite3'
if not os.path.isfile(database): raise RuntimeError('Retained database is missing; no new database was created')
db=sqlite3.connect('file:'+database+'?mode=rw',uri=True)
try:
 db.execute('BEGIN IMMEDIATE')
 db.execute('DELETE FROM sessions')
 db.commit()
finally: db.close()
fd,temp=tempfile.mkstemp(prefix='.config-',dir=root)
try:
 os.fchmod(fd,0o600)
 with os.fdopen(fd,'w') as f: json.dump(runtime,f);f.flush();os.fsync(f.fileno())
 os.replace(temp,root+'/config.json')
 directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
 try: os.fsync(directory)
 finally: os.close(directory)
finally:
 if os.path.exists(temp): os.unlink(temp)
'''
    target.docker("run", "--rm", "-i", "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--mount", f"type=volume,source={target.config.volume},target=/data", "--entrypoint", "python", image,
        "-c", script, payload=json.dumps(runtime).encode())


def initialize(target, args, image):
    cfg = target.config
    username = args.admin_username
    if not re.fullmatch(r"[a-z0-9_.-]{3,32}", username):
        raise SetupError("Administrator username uses 3–32 lowercase letters, numbers, dots, underscores, or hyphens.")
    password = get_password(args.password_file, interactive=not args.non_interactive and sys.stdin.isatty())
    command = ["volume", "create"]
    for key, value in cfg.labels.items():
        command += ["--label", f"{key}={value}"]
    target.docker(*command, cfg.volume)
    target.require_owned(target.volume_info(cfg.volume))
    script = r'''
import io,os,sys,tarfile,tempfile
if os.listdir('/data'): raise RuntimeError('New data volume is not empty; bootstrap refused')
stage=tempfile.mkdtemp(prefix='.opensw-initialize-',dir='/data')
with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read())) as archive:
 if {m.name for m in archive.getmembers()} != {'config.json','bootstrap.json'}: raise RuntimeError('Invalid private bootstrap payload')
 for member in archive.getmembers():
  if not member.isfile(): raise RuntimeError('Invalid bootstrap member')
  path=stage+'/'+member.name
  fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'wb') as output:
   output.write(archive.extractfile(member).read());output.flush();os.fsync(output.fileno())
   os.fchown(output.fileno(),10001,10001)
 directory=os.open(stage,os.O_RDONLY|os.O_DIRECTORY)
 try: os.fsync(directory);os.fchown(directory,10001,10001)
 finally: os.close(directory)
 os.rename(stage,'/data/state')
 directory=os.open('/data',os.O_RDONLY|os.O_DIRECTORY)
 try:
  os.fchmod(directory,0o700);os.fchown(directory,10001,10001);os.fsync(directory)
 finally: os.close(directory)
'''
    target.docker("run", "--rm", "-i", "--network", "none", "--user", "0:0", "--cap-drop", "ALL", "--cap-add", "CHOWN",
                  "--security-opt", "no-new-privileges", "--mount", f"type=volume,source={cfg.volume},target=/data",
                  "--entrypoint", "python", image, "-c", script, payload=bootstrap_archive(cfg, username, password))
    target.stored_runtime()
    return password


def install(target: DockerTarget, args):
    from tools import backups
    from tools.gateway import Gateway
    cfg = target.config
    upgrading = args.command in {"upgrade", "reconfigure"}
    print(f"Installing {APPS[cfg.app]['name']} for {cfg.team_name}: {cfg.public_url}")
    if target.docker("image", "inspect", HELPER, check=False).returncode:
        target.docker("pull", HELPER, timeout=300)
    observed = target.container(cfg.deployment)
    if observed:
        validate_container(target, observed, allow_network_transition=upgrading)
        old_image = observed["Image"]
        target.docker("image", "inspect", old_image)
        if not upgrading and image_version(target, old_image) != CATALOG["version"]:
            raise SetupError("This installation uses an earlier source version. Run ./install.sh upgrade --config " + shlex.quote(str(args.config)) + ". Ordinary install preserves its image and does not wait for a newer health version.")
    elif upgrading:
        raise SetupError("Explicit upgrade requires the retained application container and its old immutable image. Inspect missing resources first.")
    if observed and not upgrading:
        image, digest = observed["Image"], observed.get("Config", {}).get("Labels", {}).get("io.opensw.source-sha256", "installed")
    else:
        image, digest = build_image(target, args)
    gateway = Gateway(target, image) if cfg.https else None
    host_lock = gateway.lock() if gateway else contextlib.nullcontext()
    with host_lock, target.lock():
        container = target.container(cfg.deployment)
        volume = target.volume_info(cfg.volume)
        if container:
            validate_container(target, container, allow_network_transition=upgrading)
            if not observed or container["Id"] != observed["Id"]:
                raise SetupError("The selected container changed while setup waited for its lock. Inspect it before retrying.")
            if not volume:
                raise SetupError("Retained data volume is missing. No replacement database was created.")
        old_runtime = None
        if volume:
            target.require_owned(volume)
            old_runtime = target.stored_runtime(allow_origin_change=upgrading)
        verification_password = None
        network = gateway.ensure() if gateway else None
        saved_gateway = gateway.active() if gateway else None
        needs_admission = gateway is not None and (upgrading or not gateway.admitted(saved_gateway))
        if cfg.https and volume:
            verification_password = get_password(args.password_file, interactive=not args.non_interactive and sys.stdin.isatty(), confirm=False,
                prompt="Existing administrator password for production API verification: ")
        if needs_admission:
            gateway.maintenance(old_runtime["public_url"] if old_runtime else None)
            check_certificate(gateway, args, maintenance=True)
        if not volume:
            verification_password = initialize(target, args, image)
        changed = False
        try:
            if upgrading:
                backups.stop(target)
                if container["State"].get("Running"):
                    target.docker("stop", "--time", "45", cfg.deployment, timeout=60)
                stopped = target.container(cfg.deployment)
                if not stopped or stopped["State"].get("Running"):
                    raise SetupError("The selected application did not confirm stopped; origin/data were not changed.")
                changed = True
                transition_runtime(target, image, cfg.runtime)
                target.docker("container", "rm", cfg.deployment)
                container = None
            if not container:
                start_app(target, image, digest, network=network)
            elif not container["State"].get("Running"):
                target.docker("start", cfg.deployment)
            wait_ready(target, timeout=args.wait_timeout)
            if cfg.backups:
                health = backups.configure(target, image, wait_timeout=args.wait_timeout)
                if health.get("status") != "healthy":
                    raise SetupError("Encrypted backup/restore validation is not healthy: " + json.dumps(health))
                print("Encrypted off-host backup and restore validation: healthy")
            if gateway:
                if needs_admission:
                    gateway.admit()
                check_certificate(gateway, args)
                from tools.verify_api import Verifier
                print("Checking every API route through trusted HTTPS using isolated test records; existing accounts/content remain unchanged.")
                verifier = Verifier(cfg.app, cfg.public_url, cfg.public_url, args.admin_username, verification_password,
                                    ca_file=cfg.https.ca_file or None)
                if verifier.run():
                    raise SetupError("Public API verification failed after possible admission. Inspect the report and retained deployment; no database/code rollback was attempted.")
                del verification_password
        except Exception:
            if upgrading and changed and gateway and not gateway.possible_admission:
                backups.stop(target)
                current = target.container(cfg.deployment)
                if current:
                    validate_container(target, current)
                    if current["State"].get("Running"):
                        target.docker("stop", "--time", "45", cfg.deployment, timeout=60)
                    if target.container(cfg.deployment)["State"].get("Running"):
                        raise SetupError("Pre-admission rollback could not confirm the new app stopped. Retained maintenance requires inspection.")
                    target.docker("container", "rm", cfg.deployment)
                transition_runtime(target, image, old_runtime)
                previous_networks = observed.get("NetworkSettings", {}).get("Networks", {})
                previous_network = cfg.deployment + "-private" if cfg.deployment + "-private" in previous_networks else None
                start_app(target, old_image, observed.get("Config", {}).get("Labels", {}).get("io.opensw.source-sha256", "installed"), network=previous_network)
                if gateway:
                    gateway.load(saved_gateway)
                print("Pre-admission failure: proven old image and origin restored; sessions remain revoked.", file=sys.stderr)
            elif gateway and gateway.possible_admission:
                print("Public traffic may have been admitted. Deployment is degraded: inspect/reconcile gateway, app and backup status. Database/code were not rolled back.", file=sys.stderr)
            raise
    print(f"{APPS[cfg.app]['name']} is ready: {cfg.public_url}")
    if cfg.run_on == "my-server" and not cfg.https:
        print("For private browser access keep this SSH tunnel running: " + shlex.join(tunnel_command(cfg)))
    print("Run ./verify.sh --config " + shlex.quote(str(args.config)) + " for full API verification.")


def parser():
    result = argparse.ArgumentParser(description="OpenSW — install one independent app from public source.")
    result.add_argument("command", nargs="?", choices=["install", "upgrade", "reconfigure", "plan", "status", "stop", "logs", "backup", "restore"], default="install")
    result.add_argument("--config", type=Path, help="Your app's simple TOML configuration")
    result.add_argument("--password-file", help="Private 0600 file containing the first administrator password")
    result.add_argument("--admin-username", default="admin")
    result.add_argument("--non-interactive", action="store_true")
    result.add_argument("--wait-timeout", type=int, default=600)
    result.add_argument("--snapshot", help="Confirmed backup snapshot ID to restore")
    result.add_argument("--image-id", help="Advanced: use an already loaded immutable app image; default builds this selected release from source")
    return result


def restore_installation(target, args):
    from tools import backups
    from tools.gateway import Gateway
    from tools.verify_api import Verifier
    cfg = target.config
    if not cfg.backups:
        raise SetupError("Restore requires configured encrypted backup storage.")
    if not args.snapshot:
        raise SetupError("Restore requires --snapshot FULL_ID and the existing administrator password. The selected app is built from this source release.")
    if target.container(cfg.deployment) or target.volume_info(cfg.volume):
        raise SetupError("Restore requires a new empty target: the configured application container/data volume already exists. Original data was preserved.")
    image_id = args.image_id or build_image(target, args)[0]
    image = json.loads(target.docker("image", "inspect", image_id).stdout)[0]
    if image["Id"] != image_id or image.get("Config", {}).get("Labels", {}).get("io.opensw.app") != cfg.app:
        raise SetupError("The supplied immutable image does not belong to this app.")
    if image_version(target, image_id) != CATALOG["version"]:
        raise SetupError("The supplied image release differs from this installer. Build this selected release from source, or use the matching installer for an advanced image override.")
    password = get_password(args.password_file, interactive=not args.non_interactive and sys.stdin.isatty(), confirm=False, prompt="Existing administrator password for restored API verification: ")
    if target.docker("image", "inspect", HELPER, check=False).returncode:
        target.docker("pull", HELPER, timeout=300)
    gateway = Gateway(target, image_id) if cfg.https else None
    with gateway.lock() if gateway else contextlib.nullcontext(), target.lock():
        # Recheck the empty destination after acquiring lifecycle locks.
        if target.container(cfg.deployment) or target.volume_info(cfg.volume):
            raise SetupError("Recovery target changed while waiting for its lock; no restore was repeated.")
        network = gateway.ensure() if gateway else None
        if gateway:
            gateway.maintenance()
            check_certificate(gateway, args, maintenance=True)
        manifest = backups.restore_to_empty(target, args.snapshot, cfg.volume, image_id,
                                           new_origin=cfg.public_url if cfg.https else None, timeout=args.wait_timeout)
        # The snapshot may replace its HTTPS origin explicitly, but all four
        # other runtime values must still equal the operator's current config.
        target.stored_runtime()
        start_app(target, image_id, image["Config"]["Labels"]["io.opensw.source-sha256"], network=network)
        wait_ready(target, timeout=args.wait_timeout)
        if gateway:
            gateway.admit()
            check_certificate(gateway, args)
            verifier = Verifier(cfg.app, cfg.public_url, cfg.public_url, args.admin_username, password, ca_file=cfg.https.ca_file or None)
            code = verifier.run()
        else:
            result = target.docker("exec", "-i", cfg.deployment, "python", "/app/tools/verify_api.py", "--inside-container",
                                   payload=json.dumps({"username": args.admin_username, "password": password}).encode(), timeout=600, check=False)
            print(result.stdout.decode(errors="replace"))
            code = result.returncode
        del password
        if code:
            raise SetupError("Restored app API proof failed. Recovery resources were retained for diagnosis; no original target was touched.")
        backups.configure(target, image_id, wait_timeout=args.wait_timeout)
        print(json.dumps({"status": "verified", "url": cfg.public_url, "data_volume": cfg.volume, "manifest": manifest}, indent=2))


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if not 10 <= args.wait_timeout <= 600:
            raise SetupError("--wait-timeout must be between 10 and 600 seconds.")
        args.config = args.config or wizard()
        cfg = load_config(args.config)
        if args.command == "plan":
            print(json.dumps({"app": APPS[cfg.app]["name"], "target": cfg.run_on,
                              "server": cfg.server_address or "this computer", "url": cfg.public_url,
                              "data_volume": cfg.volume, "runtime": cfg.runtime,
                              "distribution": "Build selected app from this source checkout inside Docker"}, indent=2))
            return 0
        interactive = not args.non_interactive and sys.stdin.isatty()
        if not shutil.which("docker"):
            pause_and_check("Install Docker from https://docs.docker.com/get-started/get-docker/ and start its Linux engine.",
                            lambda: None if shutil.which("docker") else (_ for _ in ()).throw(SetupError("Docker is still unavailable.")), interactive=interactive)
        with DockerTarget(cfg) as target:
            hint = "Start Docker's Linux engine, then return here."
            if cfg.run_on == "my-server":
                hint = ("Check your server address/login/key. Install Docker on that Linux server and grant your account Docker socket access. "
                        "Verify its SSH host fingerprint through your provider, connect once normally, and unlock encrypted keys in your SSH agent (ssh-add). "
                        "Then return here. Docker setup: https://docs.docker.com/engine/install/")
            engine = None
            def check():
                nonlocal engine
                engine = target.preflight()
            pause_and_check(hint, check, interactive=interactive)
            print(f"Verified Docker engine: {engine} (server {target.server_version})")
            if args.command in {"install", "upgrade", "reconfigure"}:
                started = time.monotonic()
                install(target, args)
                print(f"Measured setup time for this run: {time.monotonic() - started:.1f} seconds.")
            elif args.command == "restore":
                restore_installation(target, args)
            else:
                container = target.container(cfg.deployment)
                if not container:
                    raise SetupError("This configured app has no installed container on the selected target.")
                validate_container(target, container)
                volume = target.volume_info(cfg.volume)
                if not volume:
                    raise SetupError("The application's data volume is missing; no mutation was attempted.")
                target.require_owned(volume)
                target.stored_runtime()
                if args.command == "status":
                    from tools import backups
                    backup_health = backups.status(target) if cfg.backups else {"status": "not_configured"}
                    tls = {"status": "private_http"}
                    if cfg.https:
                        from tools.gateway import Gateway
                        try:
                            tls = Gateway(target, container["Image"]).status(timeout=1)
                        except SetupError as exc:
                            tls = {"status": "failed", "detail": str(exc)}
                    print(json.dumps({"app": cfg.app, "container": cfg.deployment,
                                      "state": container["State"], "image_id": container["Image"],
                                      "url": cfg.public_url, "data_volume": cfg.volume,
                                      "https": tls, "backups": backup_health}, indent=2))
                    if not container["State"].get("Running") or tls["status"] == "failed" or (cfg.backups and not backup_health.get("healthy", backup_health.get("status") == "healthy")):
                        return 1
                elif args.command == "logs":
                    result = target.docker("logs", "--tail", "150", cfg.deployment)
                    print((result.stdout + result.stderr).decode(errors="replace"))
                elif args.command == "stop":
                    from tools import backups
                    with target.lock():
                        current = target.container(cfg.deployment)
                        if not current or current["Id"] != container["Id"]:
                            raise SetupError("The application changed while stop waited for its lock. No stop was issued.")
                        backups.stop(target)
                        target.docker("container", "stop", cfg.deployment)
                    print("This app is stopped. Its data and credentials are retained. Run install again with the same config to start it.")
                elif args.command == "backup":
                    from tools import backups
                    if not cfg.backups:
                        raise SetupError("Configure [backups] before requesting an encrypted backup.")
                    with target.lock():
                        current = target.container(cfg.deployment)
                        if not current or current["Id"] != container["Id"]:
                            raise SetupError("The application changed while backup waited for its lock. No capture was issued.")
                        print(json.dumps(backups.run_once(target, timeout=args.wait_timeout), indent=2))
        return 0
    except (SetupError, KeyboardInterrupt, EOFError) as exc:
        print(f"\nOpenSW setup stopped: {exc or 'Cancelled.'}\nExisting data is never automatically deleted.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
