from __future__ import annotations

import io
import contextlib
import os
from pathlib import Path
import json
import subprocess
import sys
import tarfile

import pytest

from tools import install


def engine_info(version):
    return {"OSType": "linux", "ID": "unchanged-fixture-engine", "ServerVersion": version,
            "Driver": "overlay2", "DockerRootDir": "/var/lib/docker", "NCPU": 4,
            "MemTotal": 6442450944, "Containers": 9, "Images": 4,
            "ClientInfo": {"Version": "99.0.0"}}


@pytest.mark.parametrize("version", [
    None, 28, 28.3, True, [], {}, "", "27.5.1", "27.99.99+build", "0.0.0",
    "28", "28.0", "28.0.0.1", "v28.3.2", " 28.3.2", "28.3.2 ", "28.3.2\n",
    "028.3.2", "28.03.2", "28.3.02", "28.0.0-rc.1", "29.0.0-beta+build",
    "２８.3.2", "28.٣.2", "28.3.2\u200b", "28.3.2+", "28.3.2+build..id",
    "28.3.2+build_id", "28.3.2+bé", "28.3.2+build/id",
])
def test_target_daemon_version_rejects_before_any_managed_resource_operation(monkeypatch, version):
    target = install.DockerTarget(install.load_config(install.ROOT / "apps/files/config.example.toml"))
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        assert args == ("info", "--format", "{{json .}}"), "Unsupported daemon reached a managed resource operation"
        return type("Info", (), {"stdout": json.dumps(engine_info(version)).encode()})()
    monkeypatch.setattr(target, "docker", docker)
    with pytest.raises(install.SetupError, match="Update Docker.*without deleting.*volumes"):
        target.preflight()
    assert calls == [("info", "--format", "{{json .}}")]
    assert target.server_version is None


def test_missing_daemon_version_never_uses_client_info(monkeypatch):
    target = install.DockerTarget(install.load_config(install.ROOT / "apps/files/config.example.toml"))
    info = engine_info("28.3.2"); del info["ServerVersion"]
    monkeypatch.setattr(target, "docker", lambda *args: type("Info", (), {"stdout": json.dumps(info).encode()})())
    with pytest.raises(install.SetupError, match="ServerVersion"):
        target.preflight()


@pytest.mark.parametrize("version", ["28.0.0", "28.3.2", "28.3.2+vendor.1", "28.3.2+build.001", "29.0.0", "100.0.0+build-id"])
def test_supported_stable_daemon_versions_preserve_exact_reported_identity(monkeypatch, version):
    target = install.DockerTarget(install.load_config(install.ROOT / "apps/files/config.example.toml"))
    monkeypatch.setattr(target, "docker", lambda *args: type("Info", (), {"stdout": json.dumps(engine_info(version)).encode()})())
    assert target.preflight() == "unchanged-fixture-engine"
    assert target.server_version == version


@pytest.mark.parametrize("second,succeeds", [("28.3.2", True), ("27.5.1", False)])
def test_daemon_update_prerequisite_pauses_once_and_rechecks_actual_info(monkeypatch, second, succeeds):
    target = install.DockerTarget(install.load_config(install.ROOT / "apps/files/config.example.toml"))
    versions, calls, prompts = iter(["27.5.1", second]), [], []
    def docker(*args, **kwargs):
        calls.append(args)
        assert args == ("info", "--format", "{{json .}}")
        return type("Info", (), {"stdout": json.dumps(engine_info(next(versions))).encode()})()
    monkeypatch.setattr(target, "docker", docker)
    monkeypatch.setattr("builtins.input", lambda message: prompts.append(message) or "")
    if succeeds:
        install.pause_and_check("Update target Docker without deleting volumes", target.preflight, interactive=True)
        assert target.server_version == "28.3.2"
    else:
        with pytest.raises(install.SetupError):
            install.pause_and_check("Update target Docker without deleting volumes", target.preflight, interactive=True)
    assert len(prompts) == 1 and calls == [("info", "--format", "{{json .}}")] * 2


def test_unsupported_daemon_stops_the_install_cli_before_installation(monkeypatch, capsys):
    cfg = install.load_config(install.ROOT / "apps/files/config.example.toml")
    target, calls = install.DockerTarget(cfg), []
    def docker(*args, **kwargs):
        calls.append(args)
        assert args == ("info", "--format", "{{json .}}")
        return type("Info", (), {"stdout": json.dumps(engine_info("27.5.1")).encode()})()
    monkeypatch.setattr(target, "docker", docker)
    monkeypatch.setattr(install, "DockerTarget", lambda config: contextlib.nullcontext(target))
    monkeypatch.setattr(install.shutil, "which", lambda program: "/fixture/docker")
    monkeypatch.setattr(install, "install", lambda *args: pytest.fail("Unsupported daemon reached installation"))
    monkeypatch.setattr("builtins.input", lambda message: pytest.fail("Noninteractive preflight prompted"))
    assert install.main(["install", "--config", str(install.ROOT / "apps/files/config.example.toml"), "--non-interactive"]) == 1
    assert calls == [("info", "--format", "{{json .}}")]
    assert "without deleting" in capsys.readouterr().err


@pytest.mark.parametrize("app", list(install.APPS))
def test_each_example_has_its_own_runtime_identity_and_port(app):
    cfg = install.load_config(install.ROOT / "apps" / app / "config.example.toml")
    assert cfg.app == app
    assert cfg.port == install.APPS[app]["port"]
    assert cfg.deployment == f"opensw-{app}-team"
    assert cfg.public_url == f"http://{app}-team.localhost:{cfg.port}"
    assert set(cfg.runtime) == {"app", "instance_name", "team_name", "public_url", "max_upload_mb"}


@pytest.mark.parametrize("extra", ["unknown = 1", "[advanced]\nport = true", "[advanced]\nport = 80", "[advanced]\ninstance_name = '../other'", "run_on = 'ssh'"])
def test_invalid_configuration_is_rejected_before_docker(tmp_path, extra):
    path = tmp_path / "config.toml"
    path.write_text("app = 'files'\n" + extra + "\n")
    with pytest.raises(install.SetupError):
        install.load_config(path)


def test_server_values_cannot_silently_redirect_a_local_install(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("app='files'\nserver_address='other.example'\n")
    with pytest.raises(install.SetupError, match="Server details are filled"):
        install.load_config(path)


def test_server_address_is_data_not_a_shell_command(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("app='files'\nrun_on='my-server'\nserver_username='ubuntu'\nserver_address='host; touch /tmp/evil'\n")
    with pytest.raises(install.SetupError):
        install.load_config(path)


def test_prerequisite_acknowledgment_does_not_mean_success(monkeypatch):
    attempts = []
    def missing():
        attempts.append(1)
        raise install.SetupError("Still missing")
    monkeypatch.setattr("builtins.input", lambda _: "")
    with pytest.raises(install.SetupError, match="Still missing"):
        install.pause_and_check("Install prerequisite", missing, interactive=True)
    assert len(attempts) == 2


def test_password_file_must_be_private(tmp_path):
    password = tmp_path / "password.txt"
    password.write_text("a-long-enough-password")
    password.chmod(0o644)
    with pytest.raises(install.SetupError, match="private"):
        install.get_password(str(password), interactive=False)
    password.chmod(0o600)
    assert install.get_password(str(password), interactive=False) == "a-long-enough-password"


def test_source_context_does_not_include_credentials_or_dependency_trees(tmp_path, monkeypatch):
    for name in ("Dockerfile", ".dockerignore", "backend/opensw/main.py", "web/src/main.tsx", "shared/catalog.json", "apps/files/schema.sql", "tools/verify_api.py", "operations/backup.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public source")
    for name in ("web/node_modules/private.txt", "web/.env.local", "web/.npmrc", "apps/files/config.toml", "backend/secrets/key.pem", "backend/example.local.toml"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private content")
    monkeypatch.setattr(install, "ROOT", tmp_path)
    data, digest = install.source_archive()
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        names = archive.getnames()
        contents = b"".join(archive.extractfile(name).read() for name in names)
    assert "backend/opensw/main.py" in names
    assert b"private content" not in contents
    assert len(digest) == 64


def test_foreign_resource_is_never_adopted():
    cfg = install.load_config(install.ROOT / "apps/files/config.example.toml")
    target = install.DockerTarget(cfg)
    with pytest.raises(install.SetupError, match="ownership"):
        target.require_owned({"Labels": {"some.other.owner": "true"}})


def test_resolved_credential_and_config_inputs_never_enter_build_context(tmp_path, monkeypatch):
    for name in ("Dockerfile", ".dockerignore", "backend/opensw/main.py", "web/src/main.tsx", "shared/catalog.json", "apps/files/schema.sql", "tools/verify_api.py", "operations/backup.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public source")
    key = tmp_path / "apps/files/id_ed25519"
    password = tmp_path / "backend/operator-password.txt"
    configuration = tmp_path / "web/operator-setup"
    for path in (key, password, configuration):
        path.write_text("private-input-with-an-arbitrary-name")
        path.chmod(0o600)
    os.link(key, tmp_path / "backend/hardlinked-credential")
    monkeypatch.setattr(install, "ROOT", tmp_path)
    data, _ = install.source_archive(excluded_paths={key, password, configuration})
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        names = archive.getnames()
        contents = b"".join(archive.extractfile(name).read() for name in names)
    assert "backend/opensw/main.py" in names
    assert "apps/files/id_ed25519" not in names
    assert "backend/operator-password.txt" not in names
    assert "web/operator-setup" not in names
    assert "backend/hardlinked-credential" not in names
    assert b"private-input-with-an-arbitrary-name" not in contents


def test_bootstrap_credentials_are_only_in_private_payload():
    cfg = install.load_config(install.ROOT / "apps/files/config.example.toml")
    data = install.bootstrap_archive(cfg, "admin", "private-unique-password")
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        assert archive.getnames() == ["config.json", "bootstrap.json"]
        assert all(item.mode == 0o600 for item in archive.getmembers())
        assert b"private-unique-password" not in archive.extractfile("config.json").read()


def public_configuration(tmp_path, extra=""):
    for name, content in (("credentials", "AWS_ACCESS_KEY_ID=key\nAWS_SECRET_ACCESS_KEY=secret\n"), ("password", "a-private-recovery-password")):
        path = tmp_path / name
        path.write_text(content)
        path.chmod(0o600)
    path = tmp_path / "setup.toml"
    path.write_text("app='files'\n[https]\ndomain='files.example.com'\nemail='admin@example.com'\n" + extra + "\n[backups]\nrepository='s3:https://storage.example.com/bucket'\ncredentials_file='credentials'\npassword_file='password'\n")
    return path


def test_public_settings_do_not_change_the_five_key_runtime_contract(tmp_path):
    cfg = install.load_config(public_configuration(tmp_path))
    assert cfg.public_url == "https://files.example.com"
    assert set(cfg.runtime) == {"app", "instance_name", "team_name", "public_url", "max_upload_mb"}
    assert cfg.backups.every_hours == 6 and cfg.backups.retain_days == 30
    assert cfg.backups.verify_every_days == 7
    assert cfg.https.acme_server.startswith("https://acme-v02.")


@pytest.mark.parametrize("invalid", ["domain='*.example.com'", "domain='Files.example.com'", "acme_server='http://acme.example.com/directory'", "port=80", "dns_resolvers='1.1.1.1'", "unknown=true"])
def test_public_invalid_settings_fail_before_docker(tmp_path, invalid):
    path = public_configuration(tmp_path)
    data = path.read_text()
    key = invalid.split("=")[0]
    if key == "domain":
        data = data.replace("domain='files.example.com'", invalid)
    else:
        data = data.replace("\n[backups]", "\n" + invalid + "\n[backups]")
    path.write_text(data)
    with pytest.raises(install.SetupError):
        install.load_config(path)


def test_public_https_requires_encrypted_offhost_backups(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("app='files'\n[https]\ndomain='files.example.com'\nemail='admin@example.com'\n")
    with pytest.raises(install.SetupError, match="requires.*backups"):
        install.load_config(path)


def test_every_actual_tls_backup_input_and_inode_alias_is_excluded(tmp_path, monkeypatch):
    for name in ("Dockerfile", ".dockerignore", "backend/opensw/main.py", "web/src/main.tsx", "shared/catalog.json", "apps/files/schema.sql", "tools/verify_api.py", "operations/backup.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public source")
    source = public_configuration(tmp_path)
    cfg = install.load_config(source)
    os.link(tmp_path / "credentials", tmp_path / "operations/arbitrary-name")
    monkeypatch.setattr(install, "ROOT", tmp_path)
    args = type("Args", (), {"config": source, "password_file": None})()
    data, _ = install.source_archive(excluded_paths=install.private_inputs(cfg, args))
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        names = archive.getnames()
        assert "operations/backup.py" in names
        assert "operations/arbitrary-name" not in names


def test_foreign_docker_context_environment_is_not_inherited(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://foreign.example:2375")
    monkeypatch.setenv("DOCKER_CONTEXT", "foreign")
    target = install.DockerTarget(install.load_config(install.ROOT / "apps/files/config.example.toml"))
    assert "DOCKER_HOST" not in target.env and "DOCKER_CONTEXT" not in target.env


def test_certificate_prerequisite_pauses_once_and_rechecks_tls_without_reloading(monkeypatch):
    calls, prompts = [], []
    class TLS:
        def check_tls(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise install.SetupError("DNS is not ready")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda message: prompts.append(message) or "")
    args = type("Args", (), {"wait_timeout": 10, "non_interactive": False})()
    install.check_certificate(TLS(), args, maintenance=True)
    assert len(prompts) == 1
    assert calls == [{"maintenance": True, "timeout": 10}] * 2


def test_noninteractive_certificate_prerequisite_never_prompts_or_replays(monkeypatch):
    calls = []
    class TLS:
        def check_tls(self, **kwargs):
            calls.append(kwargs)
            raise install.SetupError("DNS is not ready")
    monkeypatch.setattr("builtins.input", lambda message: pytest.fail("Noninteractive prerequisite prompted"))
    args = type("Args", (), {"wait_timeout": 10, "non_interactive": True})()
    with pytest.raises(install.SetupError, match="DNS is not ready"):
        install.check_certificate(TLS(), args, maintenance=True)
    assert calls == [{"maintenance": True, "timeout": 10}]


@pytest.mark.parametrize("publication_failed", [False, True])
def test_direct_cli_catches_the_controller_error_and_prints_no_traceback(tmp_path, publication_failed):
    # Execute the actual script as __main__. A small Docker protocol fixture
    # supplies read-only resource identity; the TLS failure is an actual closed
    # local endpoint and must be caught as the same canonical SetupError.
    path = public_configuration(tmp_path, "port=18449")
    path.write_text(path.read_text().replace("files.example.com", "files.localhost"))
    cfg = install.load_config(path)
    from tools.gateway import Gateway, NAME, CADDY, CONFIG_VOLUME, DATA_VOLUME, LABELS
    controller = Gateway(type("Target", (), {"config": cfg})(), "test")
    active = controller.candidate(Gateway.empty_config(), maintenance=False)
    saved = controller.candidate(active, maintenance=True) if publication_failed else active
    folder = tmp_path / "bin"; folder.mkdir()
    container = {"Id": "owned-app", "Image": "sha256:" + "1" * 64, "State": {"Running": True, "Status": "running"},
        "Config": {"Labels": cfg.labels}, "Mounts": [{"Name": cfg.volume, "Destination": "/data", "RW": True}],
        "HostConfig": {"PortBindings": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(cfg.port)}]}},
        "NetworkSettings": {"Networks": {cfg.deployment + "-private": {}}}}
    gateway_container = {"Id": "owned-gateway", "Config": {"Image": CADDY, "Labels": LABELS}, "State": {"Running": True},
        "HostConfig": {"Dns": None, "PortBindings": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "80"}],
            "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18449"}]}},
        "Mounts": [{"Name": DATA_VOLUME, "Destination": "/data", "RW": True}, {"Name": CONFIG_VOLUME, "Destination": "/etc/opensw", "RW": False}]}
    script = f'''#!{sys.executable}
import json,sys
args=sys.argv[1:]
if args[:2]==['--context','fixture']: args=args[2:]
if args[:2]==['context','show']: print('fixture')
elif args[:2]==['context','inspect']: print(json.dumps([{{'Endpoints':{{'docker':{{'Host':'unix:///fixture'}}}}}}]))
elif args[0]=='info': print(json.dumps({{'OSType':'linux','ID':'fixture-engine','ServerVersion':'28.3.2'}}))
elif args[:2]==['container','ls']: print(next((name for name in [{cfg.deployment!r},{NAME!r}] if 'name=^/'+name+'$' in args),''))
elif args[:2]==['container','inspect']: print(json.dumps([{gateway_container!r} if args[-1]=={NAME!r} else {container!r}]))
elif args[:2]==['volume','ls']: print(next(item[5:] for item in args if item.startswith('name=')))
elif args[:2]==['volume','inspect']: print(json.dumps([{{'Labels':{cfg.labels!r} if args[-1]=={cfg.volume!r} else {LABELS!r}}}]))
elif args[0]=='run': print(json.dumps({cfg.runtime!r} if 'sh' in args else {active!r} if {'container:' + NAME!r} in args else {saved!r}))
else: raise SystemExit('Unexpected fixture command')
'''
    docker = folder / "docker"; docker.write_text(script); docker.chmod(0o700)
    environment = dict(os.environ, PATH=str(folder) + os.pathsep + os.environ.get("PATH", ""))
    result = subprocess.run([sys.executable, str(install.ROOT / "tools/install.py"), "status", "--config", str(path), "--non-interactive"],
        input=b"", capture_output=True, env=environment, timeout=15)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr.decode()
    assert '"status": "failed"' in result.stdout.decode()
    assert ("active state differs from confirmed startup JSON" if publication_failed else "Trusted HTTPS verification failed") in result.stdout.decode()


def test_controller_error_has_exactly_one_module_owner():
    from tools.errors import SetupError
    from tools import backups, gateway, verify_installation
    assert install.SetupError is gateway.SetupError is verify_installation.SetupError is SetupError
    assert type(backups._error("failure")) is SetupError
