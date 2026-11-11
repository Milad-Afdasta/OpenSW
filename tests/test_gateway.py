"""Failure-boundary and host-route ownership checks for the HTTPS controller."""
import contextlib
import dataclasses
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from tools import install
from tools.gateway import Gateway


def config(app="files", domain="files.example.com"):
    cfg = install.load_config(install.ROOT / "apps" / app / "config.example.toml")
    return dataclasses.replace(cfg, https=install.HTTPS(domain, "admin@example.com"),
        backups=install.Backups("s3:https://storage.example.com/bucket", "/private/credentials", "/private/password"))


def gateway(cfg=None):
    return Gateway(SimpleNamespace(config=cfg or config()), "sha256:test")


def test_public_ip_uses_shortlived_issuer_and_no_sni_certificate_selection():
    controller = gateway(config(domain="203.0.113.17"))
    active = controller.candidate(Gateway.empty_config(), maintenance=True)
    assert active["apps"]["tls"]["automation"]["policies"] == [{
        "subjects": ["203.0.113.17"], "issuers": [{"module": "acme",
        "ca": install.HTTPS.acme_server, "email": "admin@example.com", "profile": "shortlived"}]}]
    assert active["apps"]["http"]["servers"]["https"]["tls_connection_policies"] == [{"default_sni": "203.0.113.17"}]
    assert controller.admitted(active) is False
    active = controller.candidate(active, maintenance=False)
    assert controller.admitted(active) is True
    active["apps"]["http"]["servers"]["https"]["tls_connection_policies"] = [{}]
    with pytest.raises(install.SetupError, match="TLS connection policy"):
        controller.admitted(active)


def test_distinct_second_ip_rejects_before_any_ca_persistence(monkeypatch):
    original = gateway(config(domain="203.0.113.17"))
    active = original.candidate(Gateway.empty_config(), maintenance=False)
    before = json.dumps(active, sort_keys=True)
    second = gateway(config("notes", "203.0.113.18"))
    monkeypatch.setattr(second, "ca_pem", lambda: pytest.fail("CA read before conflicting IP rejection"))
    monkeypatch.setattr(second, "_storage", lambda *a: pytest.fail("CA persisted before conflicting IP rejection"))
    with pytest.raises(install.SetupError, match="only one public IPv4"):
        second.candidate(active, maintenance=True)
    assert json.dumps(active, sort_keys=True) == before
    assert original.admitted(active)


def test_dns_policies_are_unchanged_and_preserved_beside_one_ip():
    dns = gateway(config("notes", "notes.example.com"))
    active = dns.candidate(Gateway.empty_config(), maintenance=False)
    policy = active["apps"]["tls"]["automation"]["policies"][0]
    assert policy == {"subjects": ["notes.example.com"], "issuers": [{"module": "acme",
        "ca": install.HTTPS.acme_server, "email": "admin@example.com"}]}
    assert active["apps"]["http"]["servers"]["https"]["tls_connection_policies"] == [{}]
    ip = gateway(config(domain="203.0.113.17"))
    active = ip.candidate(active, maintenance=False)
    assert policy in active["apps"]["tls"]["automation"]["policies"]
    assert dns.admitted(active) and ip.admitted(active)


@pytest.mark.parametrize("requested,actual,accepted", [
    ((), None, True), ((), [], True), ((), ["1.1.1.1"], False),
    ((), "", False), ((), False, False), ((), {}, False), ((), [None], False),
    (("1.1.1.1",), ["1.1.1.1"], True), (("1.1.1.1",), None, False),
    (("1.1.1.1",), [], False), (("1.1.1.1",), ["8.8.8.8"], False),
    (("1.1.1.1",), ("1.1.1.1",), False),
])
def test_owned_gateway_dns_protocol_distinguishes_default_from_explicit_settings(monkeypatch, requested, actual, accepted):
    from tools.gateway import CADDY, CONFIG_VOLUME, DATA_VOLUME, LABELS
    cfg = config()
    cfg = dataclasses.replace(cfg, https=dataclasses.replace(cfg.https, dns_resolvers=requested))
    network = cfg.deployment + "-private"
    container = {"Config": {"Labels": LABELS, "Image": CADDY}, "State": {"Running": True},
        "HostConfig": {"Dns": actual, "PortBindings": {
            "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "80"}],
            "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "443"}]}},
        "Mounts": [{"Destination": "/data", "Name": DATA_VOLUME, "RW": True},
                   {"Destination": "/etc/opensw", "Name": CONFIG_VOLUME, "RW": False}],
        "NetworkSettings": {"Networks": {network: {}}}}
    target = SimpleNamespace(config=cfg, container=lambda name: container,
        volume_info=lambda name: {"Labels": LABELS}, docker=lambda *args, **kwargs: pytest.fail("Reconciliation issued a Docker mutation"))
    controller = Gateway(target, "test")
    monkeypatch.setattr(controller, "network", lambda: network)
    monkeypatch.setattr(controller, "active", Gateway.empty_config)
    monkeypatch.setattr(controller, "_storage", lambda operation: Gateway.empty_config())
    if accepted:
        assert controller.ensure() == network
    else:
        with pytest.raises(install.SetupError, match="same DNS nameservers"):
            controller.ensure()


@pytest.mark.parametrize("scenario", ["normal", "publication_failed", "maintenance", "foreign", "mismatched_route", "mismatched_redirect", "unowned_policy"])
def test_status_proves_durable_owned_admission_before_tls_without_any_mutation(monkeypatch, scenario):
    from tools.gateway import CADDY, CONFIG_VOLUME, DATA_VOLUME, LABELS
    cfg = config()
    container = {"Config": {"Labels": LABELS, "Image": CADDY}, "State": {"Running": True},
        "HostConfig": {"Dns": None, "PortBindings": {
            "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "80"}],
            "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "443"}]}},
        "Mounts": [{"Destination": "/data", "Name": DATA_VOLUME, "RW": True},
                   {"Destination": "/etc/opensw", "Name": CONFIG_VOLUME, "RW": False}]}
    target = SimpleNamespace(config=cfg, container=lambda name: container, volume_info=lambda name: {"Labels": LABELS},
        docker=lambda *args, **kwargs: pytest.fail("Status issued a Docker mutation"))
    controller = Gateway(target, "test")
    active = controller.candidate(Gateway.empty_config(), maintenance=scenario == "maintenance")
    saved = json.loads(json.dumps(active))
    if scenario == "publication_failed":
        saved = controller.candidate(active, maintenance=True)
    elif scenario == "foreign":
        container["Config"] = {**container["Config"], "Labels": {"some.other.owner": "true"}}
    elif scenario == "mismatched_route":
        active["apps"]["http"]["servers"]["https"]["routes"][0]["handle"][0]["upstreams"][0]["dial"] = "foreign-backend:8000"
        saved = json.loads(json.dumps(active))
    elif scenario == "mismatched_redirect":
        active["apps"]["http"]["servers"]["http"]["routes"][0]["handle"][0]["headers"]["Location"] = ["https://foreign.example.com"]
        saved = json.loads(json.dumps(active))
    elif scenario == "unowned_policy":
        active["apps"]["tls"]["automation"]["policies"] = []
        active["apps"]["tls"]["certificates"]["automate"] = []
        saved = json.loads(json.dumps(active))
    reads, tls_calls = [], []
    monkeypatch.setattr(controller, "active", lambda: active)
    def storage(operation, *args):
        reads.append(operation)
        assert operation == "read", "Read-only status attempted startup publication"
        return saved
    monkeypatch.setattr(controller, "_storage", storage)
    monkeypatch.setattr(controller, "check_tls", lambda **kwargs: tls_calls.append(kwargs) or {"status": "trusted", "url": cfg.public_url})
    if scenario == "normal":
        assert controller.status() == {"status": "trusted", "url": cfg.public_url, "configuration": "confirmed", "admission": "confirmed"}
        assert reads == ["read"] and tls_calls == [{"timeout": 1}]
    else:
        with pytest.raises(install.SetupError):
            controller.status()
        assert tls_calls == [], "A trusted connection cannot hide failed durability/ownership/admission"


def test_other_app_routes_and_exact_issuer_policies_survive_promotion():
    files, notes = gateway(), gateway(config("notes", "notes.example.com"))
    active = notes.candidate(Gateway.empty_config(), maintenance=False)
    original_route = active["apps"]["http"]["servers"]["https"]["routes"][0]
    original_policy = active["apps"]["tls"]["automation"]["policies"][0]
    active = gateway(config("files", "old-files.example.com")).candidate(active, maintenance=False)
    candidate = files.candidate(active, maintenance=True, old_origin="https://old-files.example.com")
    assert original_route in candidate["apps"]["http"]["servers"]["https"]["routes"]
    assert original_policy in candidate["apps"]["tls"]["automation"]["policies"]
    assert candidate["admin"] == {"listen": "127.0.0.1:2019", "config": {"persist": False}}
    own = candidate["apps"]["http"]["servers"]["https"]["routes"][1]
    assert own["match"] == [{"host": ["files.example.com", "old-files.example.com"]}]
    assert own["handle"][0]["status_code"] == 503
    admitted = files.candidate(candidate, maintenance=False)
    assert admitted["apps"]["tls"]["certificates"]["automate"] == ["files.example.com", "notes.example.com"]
    assert original_policy in admitted["apps"]["tls"]["automation"]["policies"]
    assert all(p.get("subjects") for p in admitted["apps"]["tls"]["automation"]["policies"])


@pytest.mark.parametrize("change", ["email", "acme_server", "ca_content", "trust_field"])
def test_status_rejects_requested_certificate_policy_drift_without_publication(monkeypatch, tmp_path, change):
    ca = tmp_path / "ca.pem"; ca.write_text("original explicit trusted certificate\n")
    original = dataclasses.replace(config(), https=dataclasses.replace(config().https, ca_file=str(ca)))
    source = gateway(original)
    monkeypatch.setattr(source, "_storage", lambda *args: None)
    active = source.candidate(Gateway.empty_config(), maintenance=False)
    requested = original
    if change == "email":
        requested = dataclasses.replace(original, https=dataclasses.replace(original.https, email="changed-contact@example.com"))
    elif change == "acme_server":
        requested = dataclasses.replace(original, https=dataclasses.replace(original.https, acme_server="https://other-ca.example.com/directory"))
    elif change == "ca_content":
        ca.write_text("a different explicitly trusted certificate\n")
    else:
        active["apps"]["tls"]["automation"]["policies"][0]["issuers"][0]["trusted_roots_pem_files"] = ["/etc/opensw/foreign-ca.pem"]
    target = SimpleNamespace(config=requested, container=lambda name: {"State": {"Running": True}})
    controller = Gateway(target, "test")
    monkeypatch.setattr(controller, "validate_owned", lambda container: None)
    monkeypatch.setattr(controller, "active", lambda: active)
    def read(operation, *args):
        assert operation == "read", "Status published certificate/config bytes"
        return active
    monkeypatch.setattr(controller, "_storage", read)
    monkeypatch.setattr(controller, "check_tls", lambda **kwargs: pytest.fail("Policy mismatch reached TLS readiness"))
    with pytest.raises(install.SetupError, match="reconfigure"):
        controller.status()


def test_all_four_current_certificate_policies_match_without_republishing_ca(monkeypatch, tmp_path):
    ca = tmp_path / "ca.pem"; ca.write_text("explicit trusted certificate bytes\n")
    controllers, active = [], Gateway.empty_config()
    for app in ("files", "projects", "notes", "chat"):
        cfg = config(app, app + ".example.com")
        controller = gateway(dataclasses.replace(cfg, https=dataclasses.replace(cfg.https, ca_file=str(ca))))
        monkeypatch.setattr(controller, "_storage", lambda *args: None)
        active = controller.candidate(active, maintenance=False)
        controllers.append(controller)
    for controller in controllers:
        monkeypatch.setattr(controller, "_storage", lambda *args: pytest.fail("Read-only policy comparison published CA"))
        assert controller.admitted(active)


def test_ordinary_install_cli_rejects_changed_policy_before_password_or_mutation(monkeypatch, capsys, tmp_path):
    original = config()
    requested = dataclasses.replace(original, https=dataclasses.replace(original.https, email="changed-contact@example.com"))
    active = gateway(original).candidate(Gateway.empty_config(), maintenance=False)
    container = {"Id": "same-owned-container", "Image": "same-image", "State": {"Running": True}, "Config": {"Labels": {}}}
    calls = []
    class Target:
        config = requested
        server_version = "28.3.2"
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def preflight(self): return "same-engine"
        def container(self, name): return container
        def volume_info(self, name): return {"same": "volume"}
        def require_owned(self, resource): pass
        def stored_runtime(self, **kwargs): return requested.runtime
        def lock(self): return contextlib.nullcontext()
        def docker(self, *args, **kwargs):
            calls.append(args)
            assert args[0] == "image" and args[1] == "inspect", "Ordinary policy mismatch mutated Docker/app state"
            return SimpleNamespace(returncode=0, stdout=b"{}", stderr=b"")
    monkeypatch.setattr(install, "DockerTarget", lambda cfg: Target())
    monkeypatch.setattr(install, "load_config", lambda path: requested)
    monkeypatch.setattr(install, "validate_container", lambda *args, **kwargs: None)
    monkeypatch.setattr(install, "image_version", lambda *args: install.CATALOG["version"])
    monkeypatch.setattr(install.shutil, "which", lambda name: "/fixture/docker")
    monkeypatch.setattr(install, "get_password", lambda *args, **kwargs: pytest.fail("Policy mismatch requested credentials"))
    monkeypatch.setattr(Gateway, "lock", lambda self: contextlib.nullcontext())
    monkeypatch.setattr(Gateway, "ensure", lambda self: "same-network")
    monkeypatch.setattr(Gateway, "active", lambda self: active)
    monkeypatch.setattr(Gateway, "_storage", lambda *args: pytest.fail("Ordinary install published CA/config on policy drift"))
    assert install.main(["install", "--config", str(tmp_path / "operator.toml"), "--non-interactive"]) == 1
    assert "reconfigure" in capsys.readouterr().err
    assert calls == [("image", "inspect", install.HELPER), ("image", "inspect", "same-image")]


def test_global_route_namespaces_cover_historical_suffix_instances_and_other_apps():
    controllers = [gateway(dataclasses.replace(config(app, domain), instance_name=instance)) for app, instance, domain in (
        ("files", "foo", "a.example.com"), ("files", "foo-redirect", "b.example.com"),
        ("notes", "foo", "c.example.com"), ("notes", "foo-redirect", "d.example.com"))]
    active = Gateway.empty_config()
    for controller in controllers:
        active = controller.candidate(active, maintenance=False)
    servers = active["apps"]["http"]["servers"]
    all_routes = servers["http"]["routes"] + servers["https"]["routes"]
    identifiers = [route["@id"] for route in all_routes]
    assert len(identifiers) == len(set(identifiers)) == 8
    assert {route["@id"] for route in servers["http"]["routes"]} == {"redirect-" + controller.deployment for controller in controllers}
    assert {route["@id"] for route in servers["https"]["routes"]} == {controller.deployment for controller in controllers}
    selected = controllers[0]
    untouched = {route["@id"]: route for route in all_routes if route["@id"] not in {selected.deployment, "redirect-" + selected.deployment}}
    active = selected.candidate(active, maintenance=True)
    active = selected.candidate(active, maintenance=False)
    restarted = json.loads(json.dumps(active))
    after = {route["@id"]: route for server in restarted["apps"]["http"]["servers"].values() for route in server["routes"]}
    assert len(after) == 8
    assert all(after[identity] == route for identity, route in untouched.items())


def test_duplicate_hostname_rejects_before_any_configuration_publication():
    active = gateway(config("notes", "files.example.com")).candidate(Gateway.empty_config(), maintenance=False)
    with pytest.raises(install.SetupError, match="already owned"):
        gateway().candidate(active, maintenance=False)


def test_missing_old_https_policy_never_creates_an_implicit_default_issuer():
    with pytest.raises(install.SetupError, match="old HTTPS"):
        gateway().candidate(Gateway.empty_config(), maintenance=True, old_origin="https://old.example.com")


def test_unbounded_or_combined_issuer_policy_is_not_adopted():
    controller = gateway()
    active = controller.candidate(Gateway.empty_config(), maintenance=False)
    active["apps"]["tls"]["automation"]["policies"][0]["subjects"] = []
    with pytest.raises(install.SetupError, match="uniquely owned"):
        controller.candidate(active, maintenance=False)


def test_confirmed_maintenance_phase_can_resume_without_rebuilding_or_reseeding():
    controller = gateway()
    maintenance = controller.candidate(Gateway.empty_config(), maintenance=True)
    assert controller.admitted(maintenance) is False
    active = controller.candidate(maintenance, maintenance=False)
    assert controller.admitted(active) is True
    active["apps"]["http"]["servers"]["https"]["routes"][0]["handle"][0]["upstreams"][0]["dial"] = "foreign-app:8000"
    with pytest.raises(install.SetupError, match="differs"):
        controller.admitted(active)


def test_unknown_load_is_read_once_and_never_replayed(monkeypatch):
    controller = gateway()
    candidate, calls, saved = Gateway.empty_config(), [], []
    def admin(method, payload=None):
        calls.append(method)
        if method == "POST":
            raise TimeoutError("response lost after successful admission")
        return candidate
    def storage(operation, value=None):
        if operation == "write":
            saved.append(value)
        return saved[-1] if operation == "read" else None
    monkeypatch.setattr(controller, "_admin", admin)
    monkeypatch.setattr(controller, "_storage", storage)
    assert controller.load(candidate, admits=True) == candidate
    assert calls == ["POST", "GET", "GET"]
    assert saved == [candidate]
    assert controller.possible_admission


def test_rejected_load_keeps_last_confirmed_startup_and_does_not_retry(monkeypatch):
    controller = gateway()
    writes, calls = [], []
    def admin(method, payload=None):
        calls.append(method)
        if method == "POST":
            raise ValueError("invalid Caddy module config")
        return {"previous": True}
    monkeypatch.setattr(controller, "_admin", admin)
    monkeypatch.setattr(controller, "_storage", lambda *args: writes.append(args))
    with pytest.raises(install.SetupError, match="not active"):
        controller.load(Gateway.empty_config())
    assert calls == ["POST", "GET"] and writes == []


def test_admin_rejection_body_is_bounded_and_in_outer_actionable_error(monkeypatch):
    detail = "indexing config: duplicate ID opensw-files-foo" + "x" * 5000
    class Rejection(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(400); self.end_headers(); self.wfile.write(detail.encode())
        def log_message(self, *args): pass
    server = HTTPServer(("127.0.0.1", 0), Rejection)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    class LocalAdmin:
        config = config()
        def docker(self, *args, payload=None, **kwargs):
            script = args[-1].replace("127.0.0.1:2019", f"127.0.0.1:{server.server_port}")
            result = subprocess.run([sys.executable, "-c", script], input=payload, capture_output=True)
            if result.returncode:
                raise install.SetupError(result.stderr.decode())
            return result
    controller = Gateway(LocalAdmin(), "test")
    monkeypatch.setattr(controller, "active", lambda: {"previous": True})
    try:
        with pytest.raises(install.SetupError) as error:
            controller.load(Gateway.empty_config())
        message = str(error.value)
        assert "HTTP 400" in message and "duplicate ID opensw-files-foo" in message
        assert len(message) < 3000 and "Traceback" not in message
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_post_admission_storage_failure_never_reclassifies_as_pre_admission(monkeypatch):
    controller = gateway()
    candidate = Gateway.empty_config()
    monkeypatch.setattr(controller, "_admin", lambda *args: candidate)
    def disk_full(*args):
        raise OSError("directory fsync failed")
    monkeypatch.setattr(controller, "_storage", disk_full)
    with pytest.raises(OSError):
        controller.load(candidate, admits=True)
    assert controller.possible_admission


def test_startup_publication_uses_real_atomic_file_and_directory_durability(tmp_path):
    class LocalStorage:
        config = config()
        fail_replace = False
        fail_directory_sync = False
        def docker(self, *args, payload=None, **kwargs):
            script = args[-1].replace("/gateway", str(tmp_path))
            if self.fail_replace:
                script = script.replace("os.replace(temp,", "(_ for _ in ()).throw(OSError('disk full')); os.replace(temp,")
            if self.fail_directory_sync:
                script = script.replace("os.fsync(directory)", "(_ for _ in ()).throw(OSError('directory sync failed'))")
            result = subprocess.run([sys.executable, "-c", script], input=payload, capture_output=True)
            if result.returncode:
                raise install.SetupError(result.stderr.decode())
            return result
    target = LocalStorage()
    controller = Gateway(target, "test")
    controller._storage("write", {"confirmed": "old"})
    assert controller._storage("read") == {"confirmed": "old"}
    target.fail_replace = True
    with pytest.raises(install.SetupError, match="disk full"):
        controller._storage("write", {"confirmed": "new"})
    assert controller._storage("read") == {"confirmed": "old"}
    assert [p.name for p in tmp_path.iterdir()] == ["startup.json"]
    assert (tmp_path / "startup.json").stat().st_mode & 0o077 == 0
    target.fail_replace, target.fail_directory_sync = False, True
    with pytest.raises(install.SetupError, match="directory sync failed"):
        controller._storage("write", {"confirmed": "new"})
    # Atomic replacement completed before directory sync failed. The controller
    # may not report readiness, but the startup file is complete, never torn.
    assert controller._storage("read") == {"confirmed": "new"}


@pytest.mark.parametrize("admission_failure", [False, True])
def test_upgrade_rolls_back_only_before_possible_public_admission(monkeypatch, admission_failure):
    from tools import backups, gateway as gateway_module
    cfg, events = config(), []
    old = {"Id": "old-container", "Image": "old-image", "State": {"Running": True}, "Config": {"Labels": {}}, "NetworkSettings": {"Networks": {}}}
    class Target:
        config = cfg
        current = old
        def docker(self, *args, **kwargs):
            events.append(args)
            if args[0] == "stop":
                self.current = {**self.current, "State": {"Running": False}}
            if args[:2] == ("container", "rm"):
                self.current = None
            return SimpleNamespace(returncode=0, stdout=b"{}", stderr=b"")
        def container(self, name): return self.current
        def volume_info(self, name): return {"owned": True}
        def require_owned(self, value): pass
        def stored_runtime(self, **kwargs): return {**cfg.runtime, "public_url": "http://files-team.localhost:8081"}
        def lock(self): return contextlib.nullcontext()
    class Controller:
        possible_admission = False
        def __init__(self, *args): pass
        def lock(self): return contextlib.nullcontext()
        def ensure(self): return "private"
        def active(self): return {"old": "routes"}
        def admitted(self, active): return False
        def maintenance(self, *args): events.append("durable-maintenance")
        def check_tls(self, **kwargs): pass
        def admit(self):
            self.possible_admission = True
            raise install.SetupError("post-admission persistence failure")
        def load(self, value): events.append("rollback-gateway")
    target = Target()
    def start(target, image, digest, **kwargs):
        events.append(("start-image", image))
        target.current = {**old, "Image": image}
    def configure(*args, **kwargs):
        if not admission_failure:
            raise install.SetupError("first backup failed before admission")
        return {"status": "healthy"}
    monkeypatch.setattr(gateway_module, "Gateway", Controller)
    monkeypatch.setattr(install, "validate_container", lambda *args, **kwargs: None)
    monkeypatch.setattr(install, "build_image", lambda *args: ("new-image", "digest"))
    monkeypatch.setattr(install, "wait_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr(install, "get_password", lambda *args, **kwargs: "unit-only-admin-password")
    monkeypatch.setattr(install, "start_app", start)
    monkeypatch.setattr(install, "transition_runtime", lambda target, image, runtime: events.append(("origin", runtime["public_url"])))
    monkeypatch.setattr(backups, "stop", lambda *args: events.append("worker-stopped"))
    monkeypatch.setattr(backups, "configure", configure)
    args = SimpleNamespace(command="upgrade", config=install.ROOT / "apps/files/config.example.toml", wait_timeout=10, password_file=None, non_interactive=True)
    with pytest.raises(install.SetupError):
        install.install(target, args)
    assert events.index("durable-maintenance") < events.index("worker-stopped")
    assert (("start-image", "old-image") in events) is not admission_failure
    assert (("start-image", "new-image") in events)
