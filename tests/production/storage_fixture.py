"""Real durable Garage S3 behind verified private-CA HTTPS, for the root lab.

This is isolated test infrastructure, not a recommended production storage
topology. No resource is removed automatically. The root runner owns cleanup
using the exact resource identities atomically recorded in state.json.

Sources: Garage v2.5.0 quick-start (single-node/default-bucket bootstrap), and
Caddy's documented static TLS/auto_https/admin/persist_config options.
"""
from __future__ import annotations

import argparse
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tarfile
import tempfile


GARAGE = "dxflrs/garage:v2.5.0@sha256:677e5b758e4425a325b811c425c026bf35a4bf42f7ddbaa773de3f4fd779a5a5"
CADDY = "caddy:2.11.9-alpine@sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee"
PYTHON = "python:3.13-slim@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc"
HELPER = "busybox:1.37.0@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec"
LABEL = "io.opensw.test"
PORT = 19000
ROOT = Path(__file__).resolve().parents[2]


class FixtureError(RuntimeError):
    pass


def atomic_json(path: Path, value: dict):
    fd, name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(command, *, payload=None, timeout=60, check=True):
    env = dict(os.environ)
    for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        env.pop(key, None)
    try:
        value = subprocess.run(command, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise FixtureError("A fixture command timed out; its outcome may be unknown. Inspect recorded resource names/IDs before continuing; no retry was issued.") from None
    if check and value.returncode:
        # Garage environment contains real credentials. Never dump commands,
        # inspect payloads, provider logs or arbitrary subprocess error text.
        raise FixtureError(f"Fixture command failed (exit {value.returncode}); inspect the owned fixture privately.")
    return value


def _write(path: Path, text: str):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _certificates(directory: Path, address: str, prefix: str):
    openssl = "openssl"
    run([openssl, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(directory / "ca.key")])
    run([openssl, "req", "-new", "-x509", "-sha256", "-days", "3", "-key", str(directory / "ca.key"), "-out", str(directory / "ca.crt"),
         "-subj", f"/CN=OpenSW storage fixture {prefix}", "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign"])
    run([openssl, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(directory / "leaf.key")])
    run([openssl, "req", "-new", "-sha256", "-key", str(directory / "leaf.key"), "-out", str(directory / "leaf.csr"), "-subj", f"/CN={address}"])
    _write(directory / "leaf.ext", f"subjectAltName=IP:{address}\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n")
    run([openssl, "x509", "-req", "-sha256", "-days", "3", "-in", str(directory / "leaf.csr"), "-CA", str(directory / "ca.crt"), "-CAkey", str(directory / "ca.key"),
         "-set_serial", str(secrets.randbits(64) + 1), "-extfile", str(directory / "leaf.ext"), "-out", str(directory / "leaf.crt")])
    run([openssl, "verify", "-CAfile", str(directory / "ca.crt"), "-verify_ip", address, str(directory / "leaf.crt")])
    for name in ("ca.key", "ca.crt", "leaf.key", "leaf.crt", "leaf.csr", "leaf.ext"):
        (directory / name).chmod(0o600)


# One bounded real operation, run from the same owned outer test network as the
# DinD fixture. Normal certificate/hostname verification and SigV4 are required.
PROBE = r'''
import datetime, hashlib, hmac, json, ssl, sys, time, urllib.error, urllib.request
cfg=json.load(sys.stdin)
context=ssl.create_default_context(cadata=cfg['ca'])
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context))
deadline=time.monotonic()+90
last='starting'
while time.monotonic()<deadline:
    now=datetime.datetime.now(datetime.timezone.utc)
    date=now.strftime('%Y%m%d'); stamp=now.strftime('%Y%m%dT%H%M%SZ')
    digest=hashlib.sha256(b'').hexdigest()
    headers={'host':cfg['host'],'x-amz-content-sha256':digest,'x-amz-date':stamp}
    names=';'.join(sorted(headers))
    canonical='HEAD\n/'+cfg['bucket']+'\n\n'+''.join(k+':'+headers[k]+'\n' for k in sorted(headers))+'\n'+names+'\n'+digest
    scope=date+'/garage/s3/aws4_request'
    to_sign='AWS4-HMAC-SHA256\n'+stamp+'\n'+scope+'\n'+hashlib.sha256(canonical.encode()).hexdigest()
    def sign(key,value): return hmac.new(key,value.encode(),hashlib.sha256).digest()
    signing=sign(sign(sign(sign(('AWS4'+cfg['secret']).encode(),date),'garage'),'s3'),'aws4_request')
    signature=hmac.new(signing,to_sign.encode(),hashlib.sha256).hexdigest()
    headers['Authorization']='AWS4-HMAC-SHA256 Credential='+cfg['key']+'/'+scope+', SignedHeaders='+names+', Signature='+signature
    try:
        request=urllib.request.Request(cfg['endpoint']+'/'+cfg['bucket'],method='HEAD',headers=headers)
        with opener.open(request,timeout=5) as response:
            if response.status!=200: raise RuntimeError('unexpected HTTP status')
        print(json.dumps({'status':'ready','head_bucket':200,'tls_verified':True,'at':time.time()})); sys.exit(0)
    except (OSError,RuntimeError) as error:
        last=type(error).__name__
        time.sleep(min(2,max(0,deadline-time.monotonic())))
print(json.dumps({'status':'failed','error_class':last})); sys.exit(1)
'''


def setup(state_dir: Path, docker_prefix: list[str], prefix: str, network_name: str, endpoint_ip: str, expected_engine_id: str | None = None, *, backend_ip: str | None = None) -> dict:
    """Create/reconcile only this fixture's named, labelled resources.

    Returns paths and public operational metadata; never raw keys/passwords.
    docker_prefix example: ['docker','--config',PRIVATE,'--host','unix://...'].
    No app, DinD, gateway, host network or unrelated Docker resource is changed.
    """
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", prefix):
        raise FixtureError("Choose a unique lowercase fixture prefix (maximum 48 characters).")
    address = ipaddress.ip_address(endpoint_ip)
    if address.version != 4:
        raise FixtureError("This isolated fixture requires an IPv4 endpoint on the owned test network.")
    backend = ipaddress.IPv4Address(backend_ip) if backend_ip else ipaddress.IPv4Address(int(address) + 1)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", network_name):
        raise FixtureError("Supply the root lab's explicit owned Docker network name.")
    if not docker_prefix or Path(docker_prefix[0]).name != "docker":
        raise FixtureError("Supply an explicit Docker command prefix.")
    state_dir = Path(state_dir).expanduser()
    if state_dir.is_symlink() or state_dir.resolve().is_relative_to(ROOT):
        raise FixtureError("Fixture secrets must live in a private directory outside the source checkout.")
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if state_dir.stat().st_mode & 0o077:
        raise FixtureError("The fixture state directory must be private (chmod 700).")
    state_dir = state_dir.resolve()
    state_path = state_dir / "state.json"
    def docker(*args, **kwargs):
        return run(docker_prefix + list(args), **kwargs)
    info = json.loads(docker("info", "--format", "{{json .}}").stdout)
    if info.get("OSType") != "linux" or not info.get("ID") or (expected_engine_id and info["ID"] != expected_engine_id):
        raise FixtureError("The selected Docker engine identity/OS does not match the root lab.")
    network = json.loads(docker("network", "inspect", network_name).stdout)[0]
    if network.get("Driver") != "bridge" or network.get("Labels", {}).get(LABEL) != prefix:
        raise FixtureError("The explicit test network does not belong to this root lab prefix.")
    subnets = [ipaddress.ip_network(item["Subnet"]) for item in network.get("IPAM", {}).get("Config", []) if item.get("Subnet")]
    gateways = {item.get("Gateway") for item in network.get("IPAM", {}).get("Config", [])}
    if address == backend or any(str(item) in gateways or not any(item in subnet and item not in {subnet.network_address, subnet.broadcast_address} for subnet in subnets if subnet.version == 4) for item in (address, backend)):
        raise FixtureError("Storage endpoint/backend must be distinct usable reserved IPs in the owned test subnet.")
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if (state.get("prefix"), state.get("engine_id"), state.get("network_name"), state.get("endpoint_ip"), state.get("backend_ip")) != (prefix, info["ID"], network_name, str(address), str(backend)):
            raise FixtureError("The private fixture state belongs to another prefix, engine, network or endpoint. Nothing was adopted.")
    else:
        if any(state_dir.iterdir()):
            raise FixtureError("Fixture state is incomplete. Preserve it for inspection and choose a new empty private state directory.")
        bucket = prefix + "-backups"
        key, secret = "GK" + secrets.token_hex(16), secrets.token_hex(32)
        _write(state_dir / "garage.env", f"GARAGE_DEFAULT_ACCESS_KEY={key}\nGARAGE_DEFAULT_SECRET_KEY={secret}\nGARAGE_DEFAULT_BUCKET={bucket}\n")
        _write(state_dir / "credentials.env", f"AWS_ACCESS_KEY_ID={key}\nAWS_SECRET_ACCESS_KEY={secret}\nAWS_DEFAULT_REGION=garage\n")
        _certificates(state_dir, str(address), prefix)
        endpoint = f"https://{address}:{PORT}"
        _write(state_dir / "garage.toml", 'metadata_dir = "/storage/meta"\ndata_dir = "/storage/data"\ndb_engine = "sqlite"\nreplication_factor = 1\nrpc_bind_addr = "0.0.0.0:3901"\nrpc_public_addr = "127.0.0.1:3901"\nrpc_secret = "' + secrets.token_hex(32) + '"\n[s3_api]\ns3_region = "garage"\napi_bind_addr = "0.0.0.0:3900"\npublic_endpoint = "' + endpoint + '"\n')
        state = {"prefix": prefix, "engine_id": info["ID"], "network_name": network_name, "endpoint_ip": str(address), "backend_ip": str(backend), "bucket": bucket, "endpoint": endpoint, "resources": [], "status": "prepared"}
        atomic_json(state_path, state)
    for filename in ("garage.env", "credentials.env", "garage.toml", "ca.crt", "leaf.crt", "leaf.key"):
        file = state_dir / filename
        if file.is_symlink() or not file.is_file() or file.stat().st_mode & 0o077:
            raise FixtureError("Required private fixture input is missing, unsafe or no longer private.")
    names = {"data": prefix + "-s3-data", "config": prefix + "-s3-config", "garage": prefix + "-garage", "tls": prefix + "-s3-tls",
             "stage": prefix + "-s3-stage", "probe": prefix + "-s3-probe"}

    def owned(kind, name):
        listing = [kind, "ls"]
        if kind == "container":
            listing += ["-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"]
        else:
            listing += ["--filter", f"name={name}", "--format", "{{.Name}}"]
        if name not in docker(*listing).stdout.decode().splitlines():
            return None
        resource = json.loads(docker(kind, "inspect", name).stdout)[0]
        labels = resource.get("Config", {}).get("Labels", {}) if kind == "container" else resource.get("Labels", {})
        if labels.get(LABEL) != prefix:
            raise FixtureError("A fixture resource name is occupied by a foreign owner. Nothing was adopted or removed.")
        recorded = next((item for item in state["resources"] if item["kind"] == kind and item["name"] == name), None)
        identifier = resource["Id"] if kind == "container" else resource["Name"]
        if recorded and recorded.get("id") and (recorded["id"] != identifier or (kind == "volume" and recorded.get("created_at") != resource.get("CreatedAt"))):
            raise FixtureError("A recorded fixture resource was replaced. Root inspection is required.")
        return resource

    def record(kind, name, resource=None):
        state["resources"] = [item for item in state["resources"] if (item["kind"], item["name"]) != (kind, name)]
        state["resources"].append({"kind": kind, "name": name, "id": (resource["Id"] if kind == "container" else resource["Name"]) if resource else None,
                                   "created_at": resource.get("CreatedAt") if resource else None})
        atomic_json(state_path, state)

    # Reject foreign names before creating even an otherwise valid fixture volume.
    for kind, name in (("volume", names["data"]), ("volume", names["config"]), ("container", names["garage"]), ("container", names["tls"])):
        owned(kind, name)
    for name in (names["stage"], names["probe"]):
        if owned("container", name) or any(item["name"] == name for item in state["resources"]):
            raise FixtureError("A prior transient fixture command has an unconfirmed outcome. Root must inspect its recorded identity before continuing.")

    def transient(name, command, **kwargs):
        cid_path = state_dir / (name + ".cid")
        if cid_path.exists():
            raise FixtureError("A transient command identity file remains; root inspection is required before another command.")
        record("container", name)
        try:
            value = docker("run", "--rm", "--name", name, "--cidfile", str(cid_path), *command, **kwargs)
        except BaseException:
            if cid_path.exists():
                record("container", name, {"Id": cid_path.read_text().strip()})
            raise
        # A completed --rm run has no remaining resource. Unknown outcomes above
        # retain their exact name and, when Docker supplied it, immutable ID.
        state["resources"] = [item for item in state["resources"] if item["name"] != name]
        atomic_json(state_path, state)
        if cid_path.exists():
            cid_path.unlink()
        return value

    for name in (names["data"], names["config"]):
        existing = owned("volume", name)
        if not existing:
            record("volume", name)
            docker("volume", "create", "--label", f"{LABEL}={prefix}", name)
            existing = owned("volume", name)
        record("volume", name, existing)
    # Provision through stdin, never a Mac path bind or secret command argument.
    def publish_config():
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w") as archive:
            for name in ("garage.toml", "ca.crt", "leaf.crt", "leaf.key", "Caddyfile"):
                path = state_dir / name
                if not path.exists():
                    continue
                data = path.read_bytes()
                item = tarfile.TarInfo(name)
                item.size, item.mode = len(data), 0o600
                archive.addfile(item, io.BytesIO(data))
        transient(names["stage"], ["-i", "--label", f"{LABEL}={prefix}", "--network", "none", "--mount", f"type=volume,source={names['config']},target=/fixture", HELPER,
                  "sh", "-ec", "chmod 700 /fixture; tar -xf - -C /fixture; chmod 600 /fixture/*"], payload=payload.getvalue())
    publish_config()

    garage = owned("container", names["garage"])
    if not garage:
        record("container", names["garage"])
        docker("run", "--detach", "--name", names["garage"], "--label", f"{LABEL}={prefix}", "--network", network_name, "--ip", str(backend), "--restart", "unless-stopped", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--mount", f"type=volume,source={names['data']},target=/storage", "--mount", f"type=volume,source={names['config']},target=/fixture,readonly",
               "--env-file", str(state_dir / "garage.env"), "--env", "GARAGE_CONFIG_FILE=/fixture/garage.toml", "--entrypoint", "/garage", GARAGE, "server", "--single-node", "--default-bucket")
        garage = owned("container", names["garage"])
    if garage["Config"]["Image"] != GARAGE or garage.get("HostConfig", {}).get("PortBindings"):
        raise FixtureError("The owned Garage fixture has an unexpected image or exposed port.")
    record("container", names["garage"], garage)
    if not garage["State"].get("Running"):
        docker("start", names["garage"])
        garage = owned("container", names["garage"])
    garage_ip = garage["NetworkSettings"]["Networks"][network_name]["IPAddress"]
    if ipaddress.IPv4Address(garage_ip) != backend:
        raise FixtureError("The Garage fixture does not have its reserved backend identity.")
    caddyfile = "{\n admin off\n auto_https off\n persist_config off\n}\nhttps://" + str(address) + ":" + str(PORT) + " {\n tls /fixture/leaf.crt /fixture/leaf.key\n reverse_proxy " + garage_ip + ":3900\n}\n"
    if (state_dir / "Caddyfile").exists():
        if (state_dir / "Caddyfile").read_text() != caddyfile:
            raise FixtureError("Garage's bridge identity changed; root must reconcile the stopped TLS fixture explicitly.")
    else:
        _write(state_dir / "Caddyfile", caddyfile)
    publish_config()
    tls = owned("container", names["tls"])
    if not tls:
        record("container", names["tls"])
        docker("run", "--detach", "--name", names["tls"], "--label", f"{LABEL}={prefix}", "--network", network_name, "--ip", str(address), "--restart", "unless-stopped", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--tmpfs", "/data:rw,nosuid,nodev,size=16m", "--tmpfs", "/config:rw,nosuid,nodev,size=16m", "--mount", f"type=volume,source={names['config']},target=/fixture,readonly",
               CADDY, "caddy", "run", "--config", "/fixture/Caddyfile", "--adapter", "caddyfile")
        tls = owned("container", names["tls"])
    bindings = tls.get("HostConfig", {}).get("PortBindings", {})
    if tls["Config"]["Image"] != CADDY or bindings or tls["NetworkSettings"]["Networks"][network_name]["IPAddress"] != str(address):
        raise FixtureError("The owned TLS fixture has an unexpected image, endpoint or host binding.")
    record("container", names["tls"], tls)
    if not tls["State"].get("Running"):
        docker("start", names["tls"])
    credential_lines = dict(line.split("=", 1) for line in (state_dir / "credentials.env").read_text().splitlines())
    probe_input = {"ca": (state_dir / "ca.crt").read_text(), "key": credential_lines["AWS_ACCESS_KEY_ID"], "secret": credential_lines["AWS_SECRET_ACCESS_KEY"],
                   "endpoint": state["endpoint"], "host": f"{address}:{PORT}", "bucket": state["bucket"]}
    result = transient(names["probe"], ["-i", "--label", f"{LABEL}={prefix}", "--network", network_name, "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", PYTHON,
                       "python", "-c", PROBE], payload=json.dumps(probe_input).encode(), timeout=110, check=False)
    if result.returncode:
        state["status"] = "failed"
        atomic_json(state_path, state)
        raise FixtureError("The real S3 HEAD/TLS verification did not pass within 90 seconds. Resources and private state were retained for root inspection.")
    state.update(status="ready", readiness=json.loads(result.stdout))
    atomic_json(state_path, state)
    return {"repository": "s3:" + state["endpoint"] + "/" + state["bucket"], "endpoint": state["endpoint"], "ca_file": str(state_dir / "ca.crt"),
            "credentials_file": str(state_dir / "credentials.env"), "state_file": str(state_path), "resources": state["resources"], "engine_id": state["engine_id"], "readiness": state["readiness"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--docker-host", required=True)
    parser.add_argument("--docker-config", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--endpoint-ip", required=True)
    parser.add_argument("--backend-ip")
    parser.add_argument("--expected-engine-id")
    args = parser.parse_args(argv)
    try:
        if not args.docker_host.startswith("unix://"):
            raise FixtureError("Select the root lab's explicit local Unix Docker socket.")
        docker_prefix = ["docker", "--config", str(args.docker_config.resolve()), "--host", args.docker_host]
        print(json.dumps(setup(args.state_dir, docker_prefix, args.prefix, args.network, args.endpoint_ip, expected_engine_id=args.expected_engine_id, backend_ip=args.backend_ip), indent=2))
        return 0
    except (FixtureError, OSError, ValueError, KeyError) as error:
        print(str(error) if isinstance(error, FixtureError) else "Storage fixture setup failed; inspect its private state.", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
