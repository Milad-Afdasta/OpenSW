"""Host HTTPS controller. Confirmed startup JSON is the sole durable owner.

The Caddy admin API is reachable only from its network namespace. Caddy never
autosaves; the controller publishes only an observed active configuration using
fsync + atomic replacement. No Docker socket or application data is mounted.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import ipaddress
import json
import secrets
import ssl
import time
import urllib.error
import urllib.request
from tools.errors import SetupError

CADDY = "caddy:2.11.9-alpine@sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee"
NAME = "opensw-https-gateway"
CONFIG_VOLUME = NAME + "-config"
DATA_VOLUME = NAME + "-data"
LABELS = {"io.opensw.managed": "true", "io.opensw.role": "https-gateway"}


def expected_policy(deployment, settings, ca_pem):
    """Build exactly one origin's issuer policy without publishing anything."""
    issuer = {"module": "acme", "ca": settings.acme_server, "email": settings.email}
    try:
        ipaddress.IPv4Address(settings.domain)
    except ipaddress.AddressValueError:
        pass
    else:
        issuer["profile"] = "shortlived"
    if ca_pem is not None:
        filename = deployment + "-" + hashlib.sha256(ca_pem.encode()).hexdigest()[:16] + "-ca.pem"
        issuer["trusted_roots_pem_files"] = ["/etc/opensw/" + filename]
    return {"subjects": [settings.domain], "issuers": [issuer]}


def connection_policies(subjects):
    """IP browsers omit SNI; one listener can have only one default IP."""
    addresses = set()
    for subject in subjects:
        try:
            ipaddress.IPv4Address(subject)
        except ipaddress.AddressValueError:
            continue
        addresses.add(subject)
    if len(addresses) > 1:
        raise SetupError("One HTTPS gateway can serve only one public IPv4 address because IP browsers omit SNI. Use a separate server for each IP/app, or DNS hostnames for shared hosting. No gateway configuration was changed.")
    return [{"default_sni": next(iter(addresses))}] if addresses else [{}]


class Gateway:
    def __init__(self, target, image):
        self.target, self.image = target, image
        self.cfg = target.config
        self.deployment = self.cfg.deployment
        self.possible_admission = False

    def ca_pem(self):
        if not self.cfg.https.ca_file:
            return None
        try:
            with open(self.cfg.https.ca_file) as certificate:
                return certificate.read()
        except OSError as exc:
            raise SetupError("Cannot read the configured HTTPS trust file. Restore that private configuration input before reconfiguring.") from exc

    @contextlib.contextmanager
    def lock(self):
        name, token, ident = NAME + "-lock", secrets.token_hex(16), None
        args = ["create", "--name", name]
        for key, value in {**LABELS, "io.opensw.run": token}.items():
            args += ["--label", key + "=" + value]
        try:
            ident = self.target.docker(*args, CADDY, "version").stdout.decode().strip()
            yield
        finally:
            if ident:
                observed = self.target.container(name)
                if observed and observed["Id"] == ident and observed["Config"]["Labels"].get("io.opensw.run") == token:
                    self.target.docker("container", "rm", ident)

    def require_owned(self, resource, *, container=False):
        labels = resource.get("Config", {}).get("Labels", {}) if container else resource.get("Labels", {})
        if any(labels.get(key) != value for key, value in LABELS.items()):
            raise SetupError("A foreign resource occupies the HTTPS gateway name. Nothing was adopted or deleted.")

    def _storage(self, operation, value=None):
        script = r'''
import json,os,sys,tempfile
op,value=json.load(sys.stdin)
path='/gateway/startup.json'
if op=='read':
 print(open(path).read())
else:
 name='startup.json' if op=='write' else value['name']
 data=json.dumps(value,sort_keys=True,separators=(',',':')).encode() if op=='write' else value['data'].encode()
 fd,temp=tempfile.mkstemp(prefix='.publish-',dir='/gateway')
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'wb') as f: f.write(data);f.flush();os.fsync(f.fileno())
  os.replace(temp,'/gateway/'+name)
  directory=os.open('/gateway',os.O_RDONLY|os.O_DIRECTORY)
  try: os.fsync(directory)
  finally: os.close(directory)
 finally:
  if os.path.exists(temp): os.unlink(temp)
'''
        result = self.target.docker("run", "--rm", "-i", "--network", "none", "--user", "0:0",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--entrypoint", "python",
            "--mount", f"type=volume,source={CONFIG_VOLUME},target=/gateway" + (",readonly" if operation == "read" else ""), self.image, "-c", script,
            payload=json.dumps([operation, value]).encode())
        return json.loads(result.stdout) if operation == "read" else None

    def active(self):
        return self._admin("GET")

    def _admin(self, method, candidate=None):
        script = r'''
import json,sys,urllib.request,urllib.error
method,candidate=json.load(sys.stdin)
url='http://127.0.0.1:2019/' + ('config/' if method=='GET' else 'load')
body=None if method=='GET' else json.dumps(candidate).encode()
request=urllib.request.Request(url,data=body,method=method,headers={'Content-Type':'application/json'})
try:
 with urllib.request.urlopen(request,timeout=20) as response:
  data=response.read()
  print(data.decode() if method=='GET' else '{}')
except urllib.error.HTTPError as error:
 detail=error.read(2048).decode(errors='replace').replace('\r',' ')
 print(f'Caddy admin rejected the request (HTTP {error.code}): {detail}',file=sys.stderr)
 raise SystemExit(2)
'''
        response = self.target.docker("run", "--rm", "-i", "--network", "container:" + NAME,
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--entrypoint", "python", self.image, "-c", script,
            payload=json.dumps([method, candidate]).encode(), timeout=35)
        return json.loads(response.stdout)

    def load(self, candidate, *, admits=False):
        # An attempted final load can admit traffic even when its response is
        # lost. Callers must never roll back the DB/code after this boundary.
        if admits:
            self.possible_admission = True
        failure = None
        try:
            self._admin("POST", candidate)
        except Exception as exc:
            failure = exc
        # Read, do not replay, an unknown load outcome.
        try:
            active = self.active()
        except Exception as exc:
            raise SetupError("HTTPS load outcome is unknown. Inspect the gateway; no load retry or application rollback was issued.") from exc
        if active != candidate:
            detail = (" Caddy rejection: " + str(failure)[:2600]) if failure else ""
            raise SetupError("HTTPS candidate is not active. The last confirmed startup file was retained; inspect before retrying." + detail) from failure
        self._storage("write", active)
        self.confirmed_config(expected=active)
        return active

    @staticmethod
    def empty_config():
        return {"admin": {"listen": "127.0.0.1:2019", "config": {"persist": False}},
                "apps": {"http": {"servers": {
                    "https": {"listen": [":443"], "tls_connection_policies": [{}],
                              "automatic_https": {"disable_redirects": True}, "routes": []},
                    "http": {"listen": [":80"], "routes": []}}},
                    "tls": {"certificates": {"automate": []}, "automation": {"policies": []}}}}

    def network(self):
        name = self.deployment + "-private"
        result = self.target.docker("network", "ls", "--filter", f"name=^{name}$", "--format", "{{.Name}}")
        if name in result.stdout.decode().splitlines():
            resource = json.loads(self.target.docker("network", "inspect", name).stdout)[0]
            self.target.require_owned(resource)
            if not resource.get("Internal"):
                raise SetupError("The selected application's private network is not internal. Nothing was adopted.")
            allowed = {self.deployment, NAME}
            if any(c.get("Name") not in allowed for c in resource.get("Containers", {}).values()):
                raise SetupError("A foreign container is attached to the app's private network.")
        else:
            args = ["network", "create", "--internal"]
            for key, value in self.cfg.labels.items():
                args += ["--label", key + "=" + value]
            self.target.docker(*args, name)
        return name

    def validate_owned(self, container):
        cfg = self.cfg
        self.require_owned(container, container=True)
        bindings = container.get("HostConfig", {}).get("PortBindings", {})
        expected = {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": str(cfg.https.http_port)}],
                    "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": str(cfg.https.port)}]}
        if bindings != expected or container.get("Config", {}).get("Image") != CADDY:
            raise SetupError("Existing HTTPS gateway ports/image differ. Use the same host HTTPS ports; foreign configuration was not adopted.")
        actual_dns = container.get("HostConfig", {}).get("Dns")
        requested_dns = list(cfg.https.dns_resolvers)
        dns_matches = actual_dns == requested_dns if requested_dns else actual_dns is None or actual_dns == []
        if not dns_matches:
            raise SetupError("HTTPS apps on one host must use the same DNS nameservers. The existing gateway resolver was not changed.")
        mounts = {m["Destination"]: m for m in container.get("Mounts", [])}
        if set(mounts) != {"/data", "/etc/opensw"} or mounts["/data"].get("Name") != DATA_VOLUME or mounts["/etc/opensw"].get("Name") != CONFIG_VOLUME or mounts["/etc/opensw"].get("RW"):
            raise SetupError("HTTPS gateway mounts do not match its isolated storage contract.")
        for name in (CONFIG_VOLUME, DATA_VOLUME):
            volume = self.target.volume_info(name)
            if not volume:
                raise SetupError("HTTPS gateway storage is missing; it will not be recreated over retained state.")
            self.require_owned(volume)

    def ensure(self):
        cfg = self.cfg
        if not cfg.https:
            raise SetupError("HTTPS gateway requires an HTTPS configuration.")
        network = self.network()
        container = self.target.container(NAME)
        if container:
            self.validate_owned(container)
            if not container["State"].get("Running"):
                self.target.docker("start", NAME)
        else:
            # Docker itself atomically rejects occupied host ports, including
            # non-Docker listeners. A failed run is never blindly repeated.
            for name in (CONFIG_VOLUME, DATA_VOLUME):
                volume = self.target.volume_info(name)
                if volume:
                    self.require_owned(volume)
                else:
                    args = ["volume", "create"]
                    for key, value in LABELS.items():
                        args += ["--label", key + "=" + value]
                    self.target.docker(*args, name)
            # Retained startup state must survive a removed gateway container.
            try:
                saved = self._storage("read")
            except Exception:
                contents = self.target.docker("run", "--rm", "--network", "none", "--entrypoint", "python",
                    "--mount", f"type=volume,source={CONFIG_VOLUME},target=/gateway", self.image, "-c",
                    "import os; print(len(os.listdir('/gateway')))").stdout.decode().strip()
                if contents != "0":
                    raise SetupError("Retained gateway startup state is unreadable. Refusing to overwrite it.")
                saved = self.empty_config()
                self._storage("write", saved)
            args = ["run", "--detach", "--name", NAME, "--restart", "unless-stopped", "--read-only",
                    "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                    "--publish", f"0.0.0.0:{cfg.https.http_port}:80", "--publish", f"0.0.0.0:{cfg.https.port}:443",
                    "--mount", f"type=volume,source={CONFIG_VOLUME},target=/etc/opensw,readonly",
                    "--mount", f"type=volume,source={DATA_VOLUME},target=/data"]
            for key, value in LABELS.items():
                args += ["--label", key + "=" + value]
            for resolver in cfg.https.dns_resolvers:
                args += ["--dns", resolver]
            self.target.docker(*args, CADDY, "caddy", "run", "--config", "/etc/opensw/startup.json")
        current = self.target.container(NAME)
        if network not in current.get("NetworkSettings", {}).get("Networks", {}):
            self.target.docker("network", "connect", network, NAME)
        deadline = time.monotonic() + 30
        while True:
            try:
                active = self.active()
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
                continue
            self.confirmed_config(expected=active)
            break
        return network

    def confirmed_config(self, *, expected=None):
        saved, active = self._storage("read"), self.active()
        if saved != active or (expected is not None and active != expected):
            raise SetupError("Gateway active state differs from confirmed startup JSON. Deployment is degraded: reconcile the interrupted publication; do not roll back application data/code after possible admission.")
        self.policy_domains(active)
        return active

    def status(self, *, timeout=1):
        container = self.target.container(NAME)
        if not container:
            raise SetupError("The configured HTTPS gateway is missing; no durable admission verdict is available.")
        self.validate_owned(container)
        if not container["State"].get("Running"):
            raise SetupError("The HTTPS gateway is stopped; startup/admission are not currently confirmed.")
        active = self.confirmed_config()
        if not self.admitted(active):
            raise SetupError("This application's confirmed gateway route remains in maintenance; public admission has not completed.")
        tls = self.check_tls(timeout=timeout)
        return {**tls, "configuration": "confirmed", "admission": "confirmed"}

    def policy_domains(self, active):
        if active.get("admin") != {"listen": "127.0.0.1:2019", "config": {"persist": False}}:
            raise SetupError("Gateway localhost admin/autosave ownership differs. The configuration was not adopted.")
        tls = active["apps"]["tls"]
        policies = tls["automation"]["policies"]
        policy_domains = set()
        for policy in policies:
            subjects = policy.get("subjects", [])
            if len(subjects) != 1 or subjects[0] in policy_domains:
                raise SetupError("Gateway certificate policies must have one uniquely owned hostname each. Unbounded/combined policies were not adopted.")
            if len(policy.get("issuers", [])) != 1 or policy["issuers"][0].get("module") != "acme":
                raise SetupError("Gateway certificate policy lacks its explicit ACME issuer owner.")
            policy_domains.add(subjects[0])
        hosts = [host for route in active["apps"]["http"]["servers"]["https"]["routes"] for match in route.get("match", []) for host in match.get("host", [])]
        if len(hosts) != len(set(hosts)) or set(hosts) != policy_domains or set(tls["certificates"].get("automate", [])) != policy_domains:
            raise SetupError("Gateway certificate subjects lack exact issuer ownership. Restore the confirmed configuration before proceeding.")
        if active["apps"]["http"]["servers"]["https"].get("tls_connection_policies") != connection_policies(policy_domains):
            raise SetupError("Gateway TLS connection policy differs from its owned certificate subjects. Restore the confirmed configuration before proceeding.")
        return policy_domains

    def candidate(self, active, *, maintenance, old_origin=None):
        result = copy.deepcopy(active)
        servers = result["apps"]["http"]["servers"]
        tls = result["apps"]["tls"]
        identity = self.deployment
        domains = [self.cfg.https.domain]
        if maintenance and old_origin and old_origin.startswith("https://"):
            from urllib.parse import urlsplit
            old_domain = urlsplit(old_origin).hostname
            if old_domain not in domains:
                domains.append(old_domain)
        own_routes = [r for r in servers["https"]["routes"] if r.get("@id") == identity]
        old_domains = {d for r in own_routes for m in r.get("match", []) for d in m.get("host", [])}
        policies = tls["automation"]["policies"]
        policy_domains = self.policy_domains(active)
        if maintenance and old_origin and old_origin.startswith("https://"):
            from urllib.parse import urlsplit
            old_domain = urlsplit(old_origin).hostname
            if old_domain != self.cfg.https.domain and (old_domain not in old_domains or old_domain not in policy_domains):
                raise SetupError("The retained old HTTPS hostname route/issuer policy is missing. Recover its gateway configuration before promoting the origin.")
        for route in servers["https"]["routes"]:
            if route.get("@id") != identity and set(domains).intersection(d for m in route.get("match", []) for d in m.get("host", [])):
                raise SetupError("This HTTPS hostname is already owned by another OpenSW app. No routes were changed.")
        servers["https"]["routes"] = [r for r in servers["https"].get("routes", []) if r.get("@id") != identity]
        redirect_identity = "redirect-" + identity
        servers["http"]["routes"] = [r for r in servers["http"].get("routes", []) if r.get("@id") != redirect_identity]
        handle = [{"handler": "static_response", "status_code": 503, "body": "OpenSW maintenance. Please try again shortly.\n"}] if maintenance else [
            {"handler": "reverse_proxy", "upstreams": [{"dial": self.deployment + ":8000"}]}]
        servers["https"]["routes"].append({"@id": identity, "match": [{"host": domains}], "handle": handle, "terminal": True})
        servers["http"]["routes"].append({"@id": redirect_identity, "match": [{"host": domains}], "handle": [
            {"handler": "static_response", "status_code": 308, "headers": {"Location": [self.cfg.public_url + "{http.request.uri}"]}}], "terminal": True})
        tls["certificates"]["automate"] = sorted((set(tls["certificates"].get("automate", [])) - old_domains) | set(domains))
        # Reject ambiguous no-SNI IP selection before persisting CA material.
        servers["https"]["tls_connection_policies"] = connection_policies(tls["certificates"]["automate"])
        # Preserve other apps' exact certificate policy, including private CAs.
        policies[:] = [p for p in policies if not set(p.get("subjects", [])).intersection(old_domains)]
        pem = self.ca_pem()
        requested_policy = expected_policy(identity, self.cfg.https, pem)
        if pem is not None:
            # Reconfiguration may retain the old hostname temporarily. Its
            # existing policy must keep its original trust bytes throughout
            # that maintenance window, including a crash/restart.
            filename = requested_policy["issuers"][0]["trusted_roots_pem_files"][0].rsplit("/", 1)[1]
            self._storage("ca", {"name": filename, "data": pem})
        # During hostname promotion retain the old hostname's existing issuer
        # rather than requesting its replacement with the new app policy.
        for policy in active["apps"]["tls"]["automation"]["policies"]:
            retained = set(policy.get("subjects", [])) & (set(domains) - {self.cfg.https.domain})
            if retained:
                preserved = copy.deepcopy(policy)
                preserved["subjects"] = sorted(retained)
                policies.append(preserved)
        policies.append(requested_policy)
        return result

    def maintenance(self, old_origin=None):
        return self.load(self.candidate(self.active(), maintenance=True, old_origin=old_origin))

    def admitted(self, active):
        self.policy_domains(active)
        routes = [route for route in active["apps"]["http"]["servers"]["https"]["routes"] if route.get("@id") == self.deployment]
        if not routes:
            return False
        if len(routes) != 1:
            raise SetupError("The gateway has duplicated application route identity. Reconcile it before continuing.")
        policy = expected_policy(self.deployment, self.cfg.https, self.ca_pem())
        current = [item for item in active["apps"]["tls"]["automation"]["policies"] if item.get("subjects") == [self.cfg.https.domain]]
        if current != [policy]:
            raise SetupError("The configured HTTPS certificate policy differs from the confirmed running policy. Run ./install.sh reconfigure --config YOUR_FILE (or upgrade) to apply the requested ACME server, contact email or trusted CA explicitly.")
        route = routes[0]
        if route.get("handle") == [{"handler": "static_response", "status_code": 503, "body": "OpenSW maintenance. Please try again shortly.\n"}]:
            return False
        expected = {"@id": self.deployment, "match": [{"host": [self.cfg.https.domain]}], "handle": [
            {"handler": "reverse_proxy", "upstreams": [{"dial": self.deployment + ":8000"}]}], "terminal": True}
        if route != expected:
            raise SetupError("The application's active proxy route differs from its confirmed origin/upstream contract. It was not silently replaced.")
        redirect = {"@id": "redirect-" + self.deployment, "match": [{"host": [self.cfg.https.domain]}], "handle": [
            {"handler": "static_response", "status_code": 308, "headers": {"Location": [self.cfg.public_url + "{http.request.uri}"]}}], "terminal": True}
        redirects = [item for item in active["apps"]["http"]["servers"]["http"]["routes"] if item.get("@id") == redirect["@id"]]
        if redirects != [redirect]:
            raise SetupError("The application's HTTP redirect differs from its confirmed HTTPS origin contract.")
        return True

    def admit(self):
        return self.load(self.candidate(self.active(), maintenance=False), admits=True)

    def check_tls(self, *, maintenance=False, timeout=120):
        from tools.install import CATALOG
        context = ssl.create_default_context(cafile=self.cfg.https.ca_file or None)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
        deadline, last = time.monotonic() + timeout, ""
        while True:
            try:
                try:
                    response = opener.open(self.cfg.public_url + "/api/health", timeout=5)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    body = response.read()
                    if maintenance and response.status == 503:
                        return {"status": "trusted", "url": self.cfg.public_url, "maintenance": True}
                    if not maintenance and response.status == 200:
                        value = json.loads(body)
                        if value.get("status") == "ok" and value.get("app") == self.cfg.app and value.get("version") == CATALOG["version"] and value.get("schema_version") == 1:
                            return {"status": "trusted", "url": self.cfg.public_url}
                    last = f"HTTPS returned {response.status}; selected app identity was not confirmed."
            except (OSError, ValueError, urllib.error.URLError) as exc:
                last = str(exc)
            if time.monotonic() >= deadline:
                raise SetupError("Trusted HTTPS verification failed: " + last + ". Check DNS A/AAAA, challenge ports, firewall and the explicitly configured CA.")
            time.sleep(2)
