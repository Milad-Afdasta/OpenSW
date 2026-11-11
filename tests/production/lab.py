"""Isolated real ACME/S3/Docker test infrastructure; never a production CA.

The parent Docker-in-Docker container bounds the four apps and their operational
workers to 4 CPUs and 6 GiB. Client, ACME, and off-host storage fixtures are
outside that envelope. All private state must live outside this checkout.
"""
from __future__ import annotations

import argparse
import base64
import io
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import tarfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
DIND = "docker:28.3.2-dind@sha256:44383404ebf0c36243f5969f0dddd23c204ea3bb185e7473a4141f6ccfd07b53"
PEBBLE = "ghcr.io/letsencrypt/pebble:2.10.1@sha256:ddf230642b1a584f519f32e347de1b05a6e4c1f6c35c1863b33effeab5f78199"
DNS = "ghcr.io/letsencrypt/pebble-challtestsrv:2.10.1@sha256:12ce21884def456bcf9786542113949e1f19dc7738d2c70e156c2d0c38a1405b"
HELPER = "busybox:1.37.0@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec"
PYTHON = "python:3.13-slim@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc"
APPS = ("files", "projects", "notes", "chat")


def command(args, *, payload=None, timeout=90):
    result = subprocess.run(args, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{shlex.join(args[:5])}: {result.stderr.decode(errors='replace')[-1600:]}")
    return result.stdout


def save(path: Path, value):
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(value, handle, indent=2); handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


class Lab:
    def __init__(self, directory: Path, *, endpoint=None):
        self.directory = directory.resolve()
        if self.directory.is_relative_to(ROOT):
            raise RuntimeError('Private lab state must be outside the source checkout.')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self.path = self.directory / 'lab.json'
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            'prefix': 'opensw-prod-' + secrets.token_hex(4), 'resources': [], 'engines': {},
        }
        config = self.directory / 'docker-public'; config.mkdir(mode=0o700, exist_ok=True)
        save(config / 'config.json', {'auths': {}})
        # Public pulls must not invoke an interactive desktop keychain helper.
        plugins = config / 'cli-plugins'; plugins.mkdir(exist_ok=True)
        for name in ('docker-buildx', 'docker-compose'):
            for source in (Path('/Applications/Docker.app/Contents/Resources/cli-plugins') / name, Path.home() / '.docker/cli-plugins' / name):
                if source.exists() and not (plugins / name).exists():
                    (plugins / name).symlink_to(source.resolve()); break
        if endpoint is None:
            context = command(['docker', 'context', 'show']).decode().strip()
            endpoint = json.loads(command(['docker', 'context', 'inspect', context]))[0]['Endpoints']['docker']['Host']
        if not endpoint.startswith('unix://'):
            raise RuntimeError('The privileged test lab requires an explicitly selected local Linux Docker engine.')
        self.docker = ['docker', '--config', str(config), '--host', endpoint]
        info = json.loads(self.run('info', '--format', '{{json .}}'))
        if info['OSType'] != 'linux': raise RuntimeError('A Linux-container Docker engine is required.')
        previous = self.state.get('outer_engine')
        if previous is not None and previous != info['ID']: raise RuntimeError('The outer engine changed; refusing lab adoption.')
        self.state.update(outer_engine=info['ID'], endpoint=endpoint, docker_config=str(config),
            host_resources={k: info.get(k) for k in ('NCPU', 'MemTotal', 'Architecture', 'ServerVersion')})
        self.labels = {'io.opensw.test': self.state['prefix']}
        self.network = self.state['prefix'] + '-net'
        network = self.inspect('network', self.network)
        if network is None:
            identifiers = self.run('network', 'ls', '--format', '{{.ID}}').decode().split()
            existing = json.loads(self.run('network', 'inspect', *identifiers)) if identifiers else []
            used = [ipaddress.ip_network(spec['Subnet']) for item in existing for spec in (item.get('IPAM', {}).get('Config') or []) if spec.get('Subnet') and ':' not in spec['Subnet']]
            subnet = next(ipaddress.ip_network(f'10.250.{index}.0/24') for index in range(1, 250) if not any(ipaddress.ip_network(f'10.250.{index}.0/24').overlaps(other) for other in used))
            self.run('network', 'create', '--subnet', str(subnet), '--label', 'io.opensw.test=' + self.state['prefix'], self.network)
            self.state['resources'].append({'kind': 'network', 'name': self.network})
            network = self.inspect('network', self.network)
        if network['Labels'].get('io.opensw.test') != self.state['prefix']:
            raise RuntimeError('Foreign fixture network; refusing adoption.')
        self.subnet = next(ipaddress.ip_network(x['Subnet']) for x in network['IPAM']['Config'] if ':' not in x['Subnet'])
        self.state['network'] = self.network
        self.persist()

    def run(self, *args, payload=None, timeout=90):
        return command(self.docker + list(args), payload=payload, timeout=timeout)

    def persist(self): save(self.path, self.state)

    def inspect(self, kind, name):
        result = subprocess.run(self.docker + [kind, 'inspect', name], capture_output=True)
        return json.loads(result.stdout)[0] if result.returncode == 0 else None

    def volume(self, suffix):
        name = self.state['prefix'] + '-' + suffix
        existing = self.inspect('volume', name)
        if existing:
            if existing.get('Labels', {}).get('io.opensw.test') != self.state['prefix']: raise RuntimeError('Foreign lab volume.')
            return name
        self.run('volume', 'create', '--label', 'io.opensw.test=' + self.state['prefix'], name)
        self.state['resources'].append({'kind': 'volume', 'name': name}); self.persist()
        return name

    def copy(self, volume, files):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as archive:
            for name, data in files.items():
                entry = tarfile.TarInfo(name); entry.size = len(data); entry.mode = 0o600
                archive.addfile(entry, io.BytesIO(data))
        self.run('run', '--rm', '-i', '--network', 'none', '--mount', f'type=volume,source={volume},target=/data',
                 HELPER, 'sh', '-ec', 'tar -xf - -C /data', payload=buffer.getvalue())

    def container(self, suffix, args, image, tail=()):
        name = self.state['prefix'] + '-' + suffix
        if self.inspect('container', name): raise RuntimeError(f'Lab container already exists: {name}; inspect before resuming.')
        identifier = self.run('run', '-d', '--name', name, '--label', 'io.opensw.test=' + self.state['prefix'],
                              '--network', self.network,
                              *args, image, *tail, timeout=180).decode().strip()
        self.state['resources'].append({'kind': 'container', 'name': name, 'id': identifier}); self.persist()
        return name

    def ip(self, name):
        result = self.inspect('container', name)
        return result['NetworkSettings']['Networks'][self.network]['IPAddress']

    def certificate(self):
        directory = self.directory / 'ca'; directory.mkdir(exist_ok=True, mode=0o700)
        root_key, root_cert = directory / 'root.key', directory / 'root.pem'
        if not root_cert.exists():
            command(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '3',
                '-subj', '/CN=OpenSW isolated lab root', '-addext', 'basicConstraints=critical,CA:TRUE',
                '-addext', 'keyUsage=critical,keyCertSign,cRLSign', '-keyout', str(root_key), '-out', str(root_cert)])
            root_key.chmod(0o600)
        key, csr, cert, ext = [directory / name for name in ('server.key', 'server.csr', 'server.pem', 'server.ext')]
        ext.write_text('subjectAltName=IP:' + str(self.subnet.network_address + 2) + ',DNS:localhost\nextendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nauthorityKeyIdentifier=keyid,issuer\nsubjectKeyIdentifier=hash\n')
        command(['openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=OpenSW lab ACME API', '-keyout', str(key), '-out', str(csr)])
        key.chmod(0o600)
        command(['openssl', 'x509', '-req', '-in', str(csr), '-CA', str(root_cert), '-CAkey', str(root_key),
            '-CAcreateserial', '-days', '3', '-sha256', '-extfile', str(ext), '-out', str(cert)])
        return root_cert, key, cert

    def start_ca(self):
        root, key, cert = self.certificate()
        api_ip = str(self.subnet.network_address + 2)
        dns = self.container('dns', ['--ip', str(self.subnet.network_address + 3), '--memory', '64m', '--cpus', '0.2', '--publish', '127.0.0.1:18055:8055'], DNS,
            ('-defaultIPv4', '127.0.0.1', '-defaultIPv6', '', '-http01', '', '-https01', '', '-tlsalpn01', ''))
        volume = self.volume('pki')
        config = {'pebble': {'listenAddress': '0.0.0.0:14000', 'managementListenAddress': '0.0.0.0:15000',
            'certificate': '/test/server.pem', 'privateKey': '/test/server.key', 'httpPort': 18880,
            'tlsPort': 18443, 'ocspResponderURL': '', 'externalAccountBindingRequired': False,
            'keyAlgorithm': 'ecdsa', 'profiles': {'default': {'description': '15-minute renewal proof', 'validityPeriod': 900}}}}
        self.copy(volume, {'server.pem': cert.read_bytes(), 'server.key': key.read_bytes(), 'pebble.json': json.dumps(config).encode()})
        ca = self.container('ca', ['--ip', api_ip, '--user', '0:0', '--memory', '256m', '--cpus', '0.5',
            '--mount', f'type=volume,source={volume},target=/test,readonly',
            '--env', 'PEBBLE_VA_NOSLEEP=1', '--env', 'PEBBLE_WFE_NONCEREJECT=0'], PEBBLE,
            ('-config', '/test/pebble.json', '-strict', '-dnsserver', self.ip(dns) + ':8053'))
        self.confirm_ca(ca, dns, root)

    def confirm_ca(self, ca, dns, root):
        api_ip = self.ip(ca)
        # Retrieve the real issued-certificate root over authenticated management TLS.
        script = "import ssl,urllib.request; print(urllib.request.urlopen('https://" + api_ip + ":15000/roots/0',context=ssl.create_default_context(cafile='/test/root.pem'),timeout=3).read().decode())"
        trust = self.volume('pki-trust'); self.copy(trust, {'root.pem': root.read_bytes()})
        last = None
        for _ in range(15):
            try:
                issued = self.run('run', '--rm', '--network', self.network, '--mount', f'type=volume,source={trust},target=/test,readonly',
                    '--entrypoint', 'python', PYTHON, '-c', script, timeout=15)
                break
            except RuntimeError as exc: last = exc; time.sleep(1)
        else: raise RuntimeError('The ACME fixture did not become ready: ' + str(last))
        bundle = self.directory / 'acme-trust.pem'; bundle.write_bytes(root.read_bytes() + issued); bundle.chmod(0o600)
        self.state['ca'] = {'container': ca, 'dns': dns, 'directory': 'https://' + api_ip + ':14000/dir', 'trust_file': str(bundle)}
        self.persist()

    def start_engine(self, role='primary', ssh_port=22284):
        if role in self.state['engines']: raise RuntimeError('This engine role already exists; inspect its state before retrying.')
        data, socket = self.volume(role + '-docker-data'), self.volume(role + '-docker-socket')
        daemon = self.container(role, ['--ip', str(self.subnet.network_address + (6 if role == 'primary' else 7)), '--privileged', '--cgroupns', 'private', '--cpus', '4', '--memory', '6g',
            '--memory-swap', '6g', '--pids-limit', '4096', '--mount', f'type=volume,source={data},target=/var/lib/docker',
            '--mount', f'type=volume,source={socket},target=/var/run', '--env', 'DOCKER_TLS_CERTDIR=',
            '--publish', '127.0.0.1:18443:18443', '--publish', '127.0.0.1:18880:18880'], DIND,
            ('dockerd', '--host=unix:///var/run/docker.sock', '--bip=10.241.0.1/24', '--storage-driver=overlay2'))
        for _ in range(60):
            result = subprocess.run(self.docker + ['exec', daemon, 'docker', 'info', '--format', '{{.ID}}'], capture_output=True, timeout=5)
            if result.returncode == 0:
                engine_id = result.stdout.decode().strip(); break
            time.sleep(1)
        else: raise RuntimeError('Nested Docker engine did not become ready; inspect the owned daemon logs.')
        key = self.directory / (role + '-ssh-key')
        command(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)]); key.chmod(0o600)
        image = self.state['prefix'] + '-ssh:fixture'
        self.run('build', '-f', str(ROOT / 'tests/installer/Dockerfile.ssh'), '-t', image, str(ROOT), timeout=300)
        ssh = self.container(role + '-ssh', ['--ip', str(self.subnet.network_address + (8 if role == 'primary' else 9)), '--mount', f'type=volume,source={socket},target=/var/run', '--publish', f'127.0.0.1:{ssh_port}:22'], image)
        self.run('cp', str(key.with_suffix('.pub')), ssh + ':/root/.ssh/authorized_keys')
        self.run('exec', ssh, 'chown', '0:0', '/root/.ssh/authorized_keys')
        self.run('exec', ssh, 'chmod', '600', '/root/.ssh/authorized_keys')
        # Trust only keys read from this exact labelled container, never a blind keyscan.
        host_keys = self.run('exec', ssh, 'sh', '-ec', 'while ! test -f /etc/ssh/ssh_host_ed25519_key.pub; do sleep 1; done; cat /etc/ssh/ssh_host_ed25519_key.pub')
        known = self.directory / (role + '-known-hosts')
        known.write_text(f'[127.0.0.1]:{ssh_port} ' + host_keys.decode().strip() + '\n'); known.chmod(0o600)
        wrapper_dir = self.directory / (role + '-bin'); wrapper_dir.mkdir(exist_ok=True, mode=0o700)
        wrapper = wrapper_dir / 'ssh'; wrapper.write_text('#!/bin/sh\nexec /usr/bin/ssh -o UserKnownHostsFile=' + shlex.quote(str(known)) + ' "$@"\n'); wrapper.chmod(0o700)
        self.state['engines'][role] = {'daemon': daemon, 'ssh': ssh, 'engine_id': engine_id, 'ssh_port': ssh_port,
            'key_file': str(key), 'path_prefix': str(wrapper_dir), 'docker_data': data}
        request = urllib.request.Request('http://127.0.0.1:18055/set-default-ipv4', data=json.dumps({'ip': self.ip(daemon)}).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=5) as response: response.read()
        self.persist()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['setup', 'recovery', 'status'])
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--docker-host')
    args = parser.parse_args(); lab = Lab(args.state_dir, endpoint=args.docker_host)
    if args.command == 'setup':
        if lab.state.get('ca'): raise RuntimeError('Lab already initialized; use status and inspect before resuming.')
        lab.start_ca(); lab.start_engine()
        print(json.dumps({'state_file': str(lab.path), 'primary_engine': lab.state['engines']['primary']['engine_id'], 'quota': '4 CPUs / 6 GiB', 'ca': 'isolated RFC8555 test authority'}))
    elif args.command == 'recovery':
        previous = lab.state['engines']['primary']['daemon']; lab.run('stop', previous, timeout=90)
        lab.start_engine('recovery', 22285)
        print('Separate empty recovery engine ready; original engine/data retained unchanged.')
    else:
        print(json.dumps({'outer_engine': lab.state['outer_engine'], 'engines': {key: {'engine_id': val['engine_id'], 'daemon': val['daemon']} for key, val in lab.state['engines'].items()}, 'resources': len(lab.state['resources'])}, indent=2))


if __name__ == '__main__':
    main()
