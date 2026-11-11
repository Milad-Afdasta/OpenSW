#!/usr/bin/env python3
"""Run real browsers outside the measured server CPU/memory cgroup.

The client shares only the selected lab daemon's network namespace. It never
mounts the daemon socket, application volumes, SSH keys, or storage credentials.
All TLS checks use the explicitly imported fixture CA and normal hostname rules.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[2]
BASE = "mcr.microsoft.com/playwright:v1.65.0-noble@sha256:6e3fb709361521eb27161606e6da6d2674e5e89fa16516cb37d6fdd91372273d"


def run(command, *, payload=None, timeout=60, check=True):
    result = subprocess.run(command, input=payload, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[-3000:])
    return result


def build(prefix, image):
    dockerfile = Path(__file__).with_name("Dockerfile.browser").read_bytes()
    digest = hashlib.sha256(dockerfile).hexdigest()
    existing = run(prefix + ["image", "inspect", image], check=False)
    if existing.returncode == 0:
        observed = json.loads(existing.stdout)[0]
        if observed.get("Config", {}).get("Labels", {}).get("io.opensw.browser-source") == digest:
            return observed["Id"]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        entry = tarfile.TarInfo("Dockerfile"); entry.size = len(dockerfile); entry.mode = 0o644; entry.mtime = 0
        archive.addfile(entry, io.BytesIO(dockerfile))
    print("Building the pinned browser client with system and NSS trust stores…", flush=True)
    result = run(prefix + ["build", "--tag", image, "--label", "io.opensw.browser-source=" + digest, "-"], payload=buffer.getvalue(), timeout=900)
    if result.stderr:
        print(result.stderr.decode(errors="replace")[-1600:], flush=True)
    return json.loads(run(prefix + ["image", "inspect", image]).stdout)[0]["Id"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab", required=True, type=Path)
    parser.add_argument("--accounts", type=Path, help="Mode-0600 flat private all-app browser config")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--engine", default="primary")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--check-only", action="store_true", help="Verify TLS/certificate/browser identity without fixture mutations")
    parser.add_argument("--app", choices=("files", "projects", "notes", "chat"))
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument("--spec", choices=("smoke.spec.cjs", "regressions.spec.cjs", "production.spec.cjs"))
    parser.add_argument("--released-ui-dir", type=Path, help="Actual frozen release's per-app static builds, mounted read-only; requires smoke spec")
    parser.add_argument("--grep")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)
    state = json.loads((args.lab / "lab.json").read_text())
    prefix = ["docker", "--config", state["docker_config"], "--host", state["endpoint"]]
    actual = json.loads(run(prefix + ["info", "--format", "{{json .}}" ]).stdout)
    if actual["ID"] != state["outer_engine"]:
        raise RuntimeError("The outer Docker engine changed; no browser resources were created")
    daemon = state["engines"][args.engine]["daemon"]
    owned = json.loads(run(prefix + ["container", "inspect", daemon]).stdout)[0]
    if owned.get("Config", {}).get("Labels", {}).get("io.opensw.test") != state["prefix"] or not owned["State"]["Running"]:
        raise RuntimeError("The selected lab daemon is not the owned running network target")
    image = build(prefix, state["prefix"] + "-browser")
    if args.build_only:
        print(json.dumps({"status": "built", "base": BASE, "image_id": image}))
        return 0
    if not args.accounts or not args.artifacts:
        raise RuntimeError("Supply --accounts PRIVATE_JSON --artifacts PRIVATE_DIRECTORY")
    accounts = args.accounts.expanduser().resolve()
    if not accounts.is_file() or accounts.stat().st_mode & 0o077 or accounts.is_relative_to(ROOT):
        raise RuntimeError("Browser accounts must be a private regular file outside the source checkout")
    selected = [args.app] if args.app else ["files", "projects", "notes", "chat"]
    configuration = json.loads(accounts.read_text())
    if any(not configuration.get(app, {}).get("url", "").startswith("https://") for app in selected):
        raise RuntimeError("Production browser checks require configured actual HTTPS origins")
    artifacts = args.artifacts.resolve()
    if artifacts.is_relative_to(ROOT):
        raise RuntimeError("Keep private browser artifacts outside the source checkout")
    artifacts.mkdir(parents=True, exist_ok=True, mode=0o700); artifacts.chmod(0o700)
    ca = Path(state["ca"]["trust_file"]).resolve()
    name = state["prefix"] + "-browser-" + secrets.token_hex(4)
    command = prefix + ["run", "--rm", "--name", name, "--label", "io.opensw.test=" + state["prefix"],
        "--label", "io.opensw.role=browser-client", "--network", "container:" + daemon,
        "--shm-size", "1g", "--security-opt", "no-new-privileges",
        "--mount", f"type=bind,source={ROOT / 'tests'},target=/repo/tests,readonly",
        "--mount", f"type=bind,source={ROOT / 'web/node_modules'},target=/repo/web/node_modules,readonly",
        "--mount", f"type=bind,source={accounts},target=/fixture/accounts.json,readonly",
        "--mount", f"type=bind,source={ca},target=/fixture/ca.pem,readonly",
        "--mount", f"type=bind,source={artifacts},target=/artifacts",
        "--env", "OPENSW_QA_CONFIG=/fixture/accounts.json", "--env", "OPENSW_BROWSER_ARTIFACTS=/artifacts/results"]
    if args.released_ui_dir:
        released = args.released_ui_dir.resolve()
        if args.spec != "smoke.spec.cjs" or any(not (released / app / "index.html").is_file() for app in selected):
            raise RuntimeError("Released UI checks require --spec smoke.spec.cjs and complete actual per-app static bundles")
        command += ["--mount", f"type=bind,source={released},target=/released-ui,readonly", "--env", "OPENSW_RELEASED_UI_DIR=/released-ui"]
    if args.app:
        command += ["--env", "OPENSW_QA_APP=" + args.app]
    if args.check_only:
        # Both Node's HTTPS client and Chromium must accept the real hostname
        # using the imported CA. The readiness page does not mutate app state.
        javascript = """
const fs=require('fs'),https=require('https');
const {chromium}=require('/repo/web/node_modules/@playwright/test');
(async()=>{const cfg=JSON.parse(fs.readFileSync('/fixture/accounts.json'));const selected=process.env.OPENSW_QA_APP?[process.env.OPENSW_QA_APP]:['files','projects','notes','chat'];const browser=await chromium.launch();const result=[];try{const context=await browser.newContext();for(const app of selected){const url=cfg[app].url+'/api/health';await new Promise((resolve,reject)=>https.get(url,response=>{let body='';response.on('data',chunk=>body+=chunk);response.on('end',()=>{const value=JSON.parse(body);response.statusCode===200&&value.app===app?resolve():reject(Error(app+' Node HTTPS readiness failed'))})}).on('error',reject));const page=await context.newPage();const response=await page.goto(url);const body=JSON.parse(await page.locator('body').innerText());if(response.status()!==200||body.app!==app)throw Error(app+' Chromium HTTPS readiness failed');result.push({app,node_tls:true,browser_tls:true,status:body.status});await page.close()}}finally{await browser.close()}console.log(JSON.stringify({status:'trusted',results:result}))})().catch(error=>{console.error(error.message);process.exitCode=1});
"""
        command += [image, "node", "-e", javascript]
    else:
        command += [image, "node", "web/node_modules/@playwright/test/cli.js", "test", "--config", "tests/browser/playwright.config.cjs"]
        if args.spec:
            command.append(args.spec)
        for project in args.project:
            command += ["--project", project]
        if args.grep:
            command += ["--grep", args.grep]
    started = time.time()
    with (artifacts / "browser.log").open("wb") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        samples = []
        try:
            deadline = time.monotonic() + args.timeout
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    # Stop only this proven named client, never its shared
                    # network owner. No test/mutation replay is attempted.
                    run(prefix + ["stop", "--time", "10", name], check=False, timeout=20)
                    raise RuntimeError("Browser run exceeded its bounded window; inspect private artifacts before rerunning unknown writes")
                sample = run(prefix + ["stats", "--no-stream", "--format", "{{json .}}", name], timeout=10, check=False)
                if sample.returncode == 0:
                    try: samples.append({"at": time.time(), **json.loads(sample.stdout)})
                    except ValueError: pass
                time.sleep(2)
        except BaseException:
            if process.poll() is None:
                observed = run(prefix + ["container", "inspect", name], check=False)
                if observed.returncode == 0:
                    client = json.loads(observed.stdout)[0]
                    if client.get("Config", {}).get("Labels", {}).get("io.opensw.test") == state["prefix"] and client["State"]["Running"]:
                        run(prefix + ["stop", "--time", "10", client["Id"]], check=False, timeout=20)
            raise
        finally:
            if process.poll() is None:
                process.wait(timeout=20)
        code = process.returncode
    report = {"status": "pass" if code == 0 else "fail", "returncode": code, "started_at": started, "ended_at": time.time(),
              "base": BASE, "image_id": image, "network_owner": daemon, "client_cpu_outside_server_quota": True,
              "app_selection": selected, "projects": args.project or ["desktop-light", "desktop-dark", "mobile-light", "mobile-dark"],
              "spec": args.spec or "all", "released_ui": bool(args.released_ui_dir),
              "client_samples": samples}
    path = artifacts / "browser-run.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output: json.dump(report, output, indent=2); output.write("\n")
    print(json.dumps({key: value for key, value in report.items() if key != "client_samples"}, indent=2))
    print("Private log: " + str(artifacts / "browser.log"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
