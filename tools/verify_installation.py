"""Run the canonical live API verifier in the selected app's installed container."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.install import APPS, ROOT, DockerTarget, get_password, load_config, validate_container
from tools.errors import SetupError
from tools.verify_api import Verifier


def choose_config() -> Path:
    saved = sorted(ROOT.glob("*.local.toml"))
    if len(saved) == 1:
        return saved[0]
    if not sys.stdin.isatty():
        raise SetupError("Pass the same --config file used for installation.")
    choices = saved or [ROOT / "apps" / app / "config.example.toml" for app in APPS]
    print("Which installation should OpenSW verify?")
    for index, path in enumerate(choices, 1):
        print(f"  {index}. {path.relative_to(ROOT)}")
    choice = input("Choose a number [1]: ").strip() or "1"
    if not choice.isdigit() or not 1 <= int(choice) <= len(choices):
        raise SetupError("Choose one of the listed installation numbers.")
    return choices[int(choice) - 1]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify an installed OpenSW app with real API workflows.")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password-file", help="Private file holding this installation's administrator password")
    args = parser.parse_args(argv)
    try:
        path = args.config or choose_config()
        cfg = load_config(path)
        print(f"OpenSW verification — {APPS[cfg.app]['name']}")
        print("The checks create clearly named test members and records, then soft-clean them up. Existing accounts and content are preserved.")
        password = get_password(args.password_file, interactive=sys.stdin.isatty(), confirm=False,
                                prompt="Administrator password: ")
        with DockerTarget(cfg) as target:
            target.preflight()
            container = target.container(cfg.deployment)
            if not container:
                raise SetupError("This app has no installed container on the configured target. Run the installer first.")
            validate_container(target, container)
            volume = target.volume_info(cfg.volume)
            if not volume:
                raise SetupError("The configured data volume is missing. No tests were run.")
            target.require_owned(volume)
            target.stored_runtime()
            if not container["State"].get("Running"):
                raise SetupError("The app is stopped. Start it with the installer before running verification.")
            if cfg.https:
                verifier = Verifier(cfg.app, cfg.public_url, cfg.public_url, args.username, password, ca_file=cfg.https.ca_file or None)
                del password
                if verifier.run():
                    return 1
                print("API verification passed through trusted HTTPS, including Secure session cookies.")
                return 0
            result = target.docker("exec", "-i", cfg.deployment, "python", "/app/tools/verify_api.py", "--inside-container",
                                   payload=json.dumps({"username": args.username, "password": password}).encode(),
                                   timeout=600, check=False)
            del password
            print(result.stdout.decode(errors="replace"))
            if result.stderr:
                print(result.stderr.decode(errors="replace"), file=sys.stderr)
            if result.returncode:
                print("\nVerification did not pass. The report above identifies the failed or blocked checks.")
                print("Inspect server logs: " + shlex.join(["./install.sh", "logs", "--config", str(path)]))
                return 1
        print("API verification passed. Optional browser checks are documented in this app's AGENTS.md.")
        return 0
    except (SetupError, KeyboardInterrupt, EOFError) as exc:
        print(f"OpenSW verification BLOCKED: {exc or 'Cancelled.'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
