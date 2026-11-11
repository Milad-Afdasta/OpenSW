#!/usr/bin/env python3
"""Source-install one disposable app, verify its API and retained restart.

Runs against the explicitly selected LOCAL Linux Docker context. The separate
real ACME/S3/workload lab is documented in this directory; this is private HTTP
CI coverage, not public HTTPS or capacity evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.install import APPS, DockerTarget, load_config


def invoke(*args):
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True, timeout=1500)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', choices=APPS, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='opensw-ci-') as directory:
        private = Path(directory)
        password = private / 'password.txt'
        password.write_text(secrets.token_urlsafe(32) + '\n')
        password.chmod(0o600)
        config_path = private / 'app.toml'
        config_path.write_text(f'app = "{args.app}"\nteam_name = "Disposable CI"\nrun_on = "this-computer"\n'
                               f'[advanced]\ninstance_name = "ci-{secrets.token_hex(5)}"\n')
        config_path.chmod(0o600)
        cfg = load_config(config_path)
        with DockerTarget(cfg) as target:
            engine = target.preflight()
            if target.container(cfg.deployment) or target.volume_info(cfg.volume):
                raise RuntimeError('Disposable CI identity unexpectedly exists; no adoption attempted.')
            try:
                install = ['tools/install.py', 'install', '--config', str(config_path), '--password-file', str(password), '--non-interactive']
                invoke(*install)
                first = target.container(cfg.deployment)
                target.require_owned(first, container=True)
                invoke('tools/verify_installation.py', '--config', str(config_path), '--password-file', str(password))
                before = target.docker('exec', cfg.deployment, 'python', '-c',
                    "import sqlite3; c=sqlite3.connect('/data/opensw.sqlite3'); print(c.execute('SELECT COUNT(*) FROM users').fetchone()[0])").stdout
                invoke('tools/install.py', 'stop', '--config', str(config_path), '--non-interactive')
                invoke(*install)
                after = target.container(cfg.deployment)
                count = target.docker('exec', cfg.deployment, 'python', '-c',
                    "import sqlite3; c=sqlite3.connect('/data/opensw.sqlite3'); print(c.execute('SELECT COUNT(*) FROM users').fetchone()[0])").stdout
                if after['Id'] != first['Id'] or after['Image'] != first['Image'] or before != count:
                    raise RuntimeError('Ordinary retained restart changed container, image, or user population.')
                print(json.dumps({'app': args.app, 'source_install': 'pass', 'all_api_routes': 'pass', 'retained_restart': 'pass'}))
            finally:
                if target.preflight() != engine:
                    raise RuntimeError('Docker target changed; disposable cleanup refused.')
                container = target.container(cfg.deployment)
                if container:
                    target.require_owned(container, container=True)
                    target.docker('rm', '--force', container['Id'])
                volume = target.volume_info(cfg.volume)
                if volume:
                    target.require_owned(volume)
                    target.docker('volume', 'rm', cfg.volume)


if __name__ == '__main__':
    main()
