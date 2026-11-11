# Reproduce the controlled production checks

This directory contains real Docker, ACME, S3, browser and HTTP workload fixtures. It is a developer validation lab, not a production certificate authority or a one-command cloud deployment. The current [validation record](../../docs/VALIDATION.md) names the tested source/images and separates completed checks from pending release gates. The [public machine summary](../../docs/evidence/v0.2.0-summary.json) contains sanitized results; keep full reports private.

Use a disposable **local** Linux-container Docker engine you explicitly control. The lab starts privileged Docker-in-Docker containers, SSH endpoints and an isolated test authority. Do not point these commands at a shared production engine. Docker must be stable **28+**, with normal protected bridge/NAT/firewall isolation. Intentional direct-routing/trusted-interface/unprotected gateway or firewall overrides are outside the supported boundary; a version check is not a host-firewall attestation. The measured original and recovery engines were 28.3.2.

Prerequisites are Python 3.11+, Docker CLI/engine, OpenSSL, OpenSSH/ssh-keygen and the repository's locked development Python dependencies ([setup](../../docs/API.md#development-checks)). Browser checks also need Node/npm and `npm ci` in `web/`. The browser runner builds its pinned Playwright image. Use the exact local Unix Docker socket shown by your verified Docker context; do not switch the global context as part of the run. Linux Docker and macOS Docker Desktop were exercised; WSL2 setup is **NOT_RUN** in this validation.

## 1. Create private fixture state

From the repository root, choose an unused private directory outside the checkout and an explicit local socket. This example socket is for Linux; Docker Desktop's actual socket may differ:

```sh
umask 077
opensw_lab_dir=$(mktemp -d "${TMPDIR:-/tmp}/opensw-production.XXXXXX")
opensw_docker_host=unix:///var/run/docker.sock
python3 tests/production/lab.py setup --state-dir "$opensw_lab_dir" --docker-host "$opensw_docker_host"
python3 tests/production/lab.py status --state-dir "$opensw_lab_dir" --docker-host "$opensw_docker_host"
```

`lab.py` creates one uniquely labelled outer network, an isolated Pebble/challtestsrv authority and an independent primary Docker engine. The primary engine's **4-CPU / 6-GiB parent quota** includes its daemon, four apps, gateway and backup workers. ACME, off-host S3 and clients run outside that quota. State records the outer/inner engine identities, owned resources, SSH keys and verified host-key wrapper paths. `setup` refuses an already initialized lab; inspect existing state rather than replaying it after an unknown outcome.

Use the existing storage fixture API to join the same labelled network at the reserved endpoint/backend addresses. This code loads the shipped modules and never prints secret values:

```sh
python3 - "$opensw_lab_dir" "$opensw_docker_host" <<'PY'
import importlib.util
from pathlib import Path
import sys

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

lab_module = load('opensw_lab', 'tests/production/lab.py')
storage_module = load('opensw_storage', 'tests/production/storage_fixture.py')
lab = lab_module.Lab(Path(sys.argv[1]), endpoint=sys.argv[2])
storage = storage_module.setup(
    lab.directory / 'storage', lab.docker, lab.state['prefix'], lab.network,
    str(lab.subnet.network_address + 4),
    expected_engine_id=lab.state['outer_engine'],
    backend_ip=str(lab.subnet.network_address + 5),
)
lab.state['storage'] = storage
lab.persist()
print('Real S3 signed HEAD and trusted TLS passed; private paths are recorded in lab.json.')
PY
```

Garage stores data durably in its own labelled volume, outside the primary daemon. A separate pinned Caddy serves private-CA HTTPS on the reserved network IP/port 19000, with no host-port publication. Successful storage setup requires signed HEAD-bucket 200 under normal CA/IP verification. Its returned repository, CA and private credentials-file paths are saved in `lab.json`. The standalone equivalent is `storage_fixture.py --state-dir PRIVATE --docker-host UNIX_SOCKET --docker-config PRIVATE_DOCKER_CONFIG --prefix LAB_PREFIX --network LAB_NETWORK --endpoint-ip RESERVED_IP [--backend-ip RESERVED_BACKEND_IP] [--expected-engine-id ID]`.

## 2. Install four independent app fixtures

Create one private TOML per app from its shipped example and use the existing [configuration guide](../../docs/CONFIGURATION.md). Keep all administrator, storage and recovery-password files mode `0600`, outside Git. Use distinct origins; accounts and data never cross apps.

For this lab, populate the existing-server settings from `lab.json`'s primary engine: address `127.0.0.1`, SSH username `root`, `key_file`, and `ssh_port`. Prefix the installer process's PATH with that engine's `path_prefix` so its generated exact-container host-key file is used. Use the lab's private `docker_config`; do not disable host-key checks or put secret values in command arguments.

Set each app's HTTPS domain to `APP.LAB_PREFIX.127.0.0.1.sslip.io`, with the actual lowercase app/prefix. Use challenge port 18880, HTTPS port 18443, and the lab's `ca.directory`/`ca.trust_file` for `acme_server`/`https.ca_file`. These alternate ports are test-only. Pebble's challenge resolver maps the synthetic hostname to the selected daemon; the control computer and namespace-sharing browser reach its loopback publication. Normal certificate/hostname verification remains enabled. Configure `[backups]` using the storage fixture's repository/credentials/CA paths plus a separate private recovery-password file.

Run the actual installer and verifier, with hidden administrator input or a private administrator password file:

```sh
./install.sh plan --config /PRIVATE/configs/files.toml
./install.sh --config /PRIVATE/configs/files.toml --password-file /PRIVATE/admin-password.txt --non-interactive
./verify.sh --config /PRIVATE/configs/files.toml --password-file /PRIVATE/admin-password.txt
./install.sh status --config /PRIVATE/configs/files.toml
```

Repeat for Projects, Notes and Chat, selecting the matching private TOML. Public setup must confirm HTTPS, its first encrypted backup/full verification and all API routes. Preserve source archive/image IDs and actual engine identity. The original measured run used `instance_name = "retained"` and a 1 MiB Files upload limit.

For historical-upgrade proof, extract the actual `v0.1.0` release into a separate private directory (`git archive --format=tar --output PRIVATE/source-v010.tar v0.1.0`, then extract it there). Install/seed it with its original private HTTP configuration first. Add HTTPS/backups only to the current operator configuration, then run current `./install.sh upgrade --config PRIVATE_TOML`. Compare original users/password hashes, complete records/revisions, schema and blob bytes before/after. An ordinary install rerun is not an upgrade. The [actual frozen contract/data fixtures](../contracts/v0.1.0/manifest.json) supplement this actual retained-data check.

## 3. Trusted browsers and the frozen released UI

Create an owned mode-`0600` JSON file outside Git, with exactly `files`, `projects`, `notes`, and `chat`; each entry has `url`, `username`, `password`. Use the actual installed HTTPS origins and disposable fixture administrator accounts. Never print or publish this JSON.

```sh
python3 tests/production/run_browser.py --lab "$opensw_lab_dir" --accounts /PRIVATE/accounts.json --artifacts /PRIVATE/browser-trust --check-only
python3 tests/production/run_browser.py --lab "$opensw_lab_dir" --accounts /PRIVATE/accounts.json --artifacts /PRIVATE/browser-current
```

The client shares the selected daemon's network namespace, with CPU/memory outside the server quota. It receives the fixture trust bundle and private browser accounts, never the Docker socket, app volumes, SSH keys or storage credentials. Node and Chromium both verify HTTPS normally. Default projects cover desktop/mobile in light/dark mode; `--app`, `--spec`, `--project` and `--grep` select explicit subsets, which must be reported as subsets. Inspect screenshots as well as real state assertions; rendering alone is not a pass.

To reproduce the released-client checks, build each app's **actual v0.1.0 static bundle** from the extracted release with its own committed lockfile, then copy the four `dist` trees into `/PRIVATE/released-ui/{files,projects,notes,chat}`. Do not rebuild current source and call it an old client:

```sh
python3 tests/production/run_browser.py --lab "$opensw_lab_dir" --accounts /PRIVATE/accounts.json --artifacts /PRIVATE/browser-v010 --released-ui-dir /PRIVATE/released-ui --spec smoke.spec.cjs
```

The published evidence contains 72 current cases and 16 actual released-UI cases. Three independent manual reviewers additionally exercised all four viewport/theme combinations for each app and rechecked the four affected Projects views after the changed source epoch. An automated suite is not a substitute for those independent reviews.

## 4. Fixed workload and captures during writes

Use the same private four-app JSON structure and an explicit trusted CA bundle. The client must reach the real configured origins. Keep the runner outside the server parent cgroup and reuse its persistent per-actor clients; do not disable verification, mock APIs or replace measured operations with synthetic sleeps.

```sh
PYTHONPATH=backend .venv/bin/python tests/production/workload.py --config /PRIVATE/accounts.json --ca-file /PRIVATE/acme-trust.pem --profile smoke --output /PRIVATE/smoke-report.json
PYTHONPATH=backend .venv/bin/python tests/production/workload.py --config /PRIVATE/accounts.json --ca-file /PRIVATE/acme-trust.pem --profile full --output /PRIVATE/full-report.json
```

Each output path must be new. `smoke` is only a 30-second diagnostic and never capacity evidence. Full provisioning stages 100 accounts per app with a 1.4-second sign-in interval, then seeds 200 real 1 MiB files, at least 500 tasks with 1 KiB descriptions, at least 500 notes with 4 KiB bodies and 10,000 messages. The fixed stages are 10/app for 30 seconds, 25/app for 30 seconds, 50/app for 300 seconds, 100/app for 30 seconds, and 10/app for 45 seconds. Four apps run concurrently. Explicit 409 conflicts, privacy rejections and deliberate login abuse have separate verdicts. Keep a **61-second quiet window after a deliberate login-abuse check** before legitimate verifier/workload sign-ins; do not weaken admission controls or hide throttling with retries.

During the logged `steady50` interval, request one capture for each selected app with `./install.sh backup --config PRIVATE_TOML`. Record exact command start/end times and returned snapshot IDs. Verify that requests/writes overlap those windows and that producer failures cannot publish success or authorize retention. Do not substitute a stopped-app archive for an online snapshot. Full validation requires each required operation kind in every stage and fails on unexpected/unconfirmed mutations or exceeded predeclared budgets.

The raw report includes runner digest, profile/model identity, exact operations/latencies, confirmed resource/version oracle, separate client CPU/RSS and budget verdicts. Record source/image/engine/OS identities and network conditions separately. For server resource reproduction, sample the **owned parent daemon cgroup** before/during/after each stage, including `cpu.max`, `cpu.stat`, `memory.max`, `memory.current`, `memory.peak` and `memory.events`, with timestamps. Use explicit root-lab Docker prefix and daemon ID; the measured run sampled at approximately two-second intervals. Record per-container IDs/restarts as diagnostics, but do not sum child memory into the already inclusive parent total. Keep raw samples/reports private and retain failed runs.

## 5. Empty-engine recovery and content audit

After saving the capture IDs and all workload evidence, finish all client activity. Run `./install.sh stop --config PRIVATE_TOML` for each original configuration and confirm its worker and app are stopped. Record original data hashes from that quiesced state. The existing recovery command then stops the primary parent while retaining its engine/data volumes, creates a separate empty engine and updates the fixture challenge target:

```sh
python3 tests/production/lab.py recovery --state-dir "$opensw_lab_dir" --docker-host "$opensw_docker_host"
```

Confirm the recovery engine is different and initially has no app containers, images or data volumes. Keep the original stopped and intact. Copy the private app configs, changing only the management target's recovery SSH key/port/wrapper (from `lab.json`) and an explicitly selected HTTPS origin if intended; preserve app/instance/team/upload identity. Storage/recovery-password material remains available outside the lost engine.

```sh
./install.sh restore --config /PRIVATE/recovery-configs/files.toml --snapshot FULL_CAPTURE_ID --password-file /PRIVATE/admin-password.txt --non-interactive
python3 tests/production/run_browser.py --lab "$opensw_lab_dir" --engine recovery --accounts /PRIVATE/recovery-accounts.json --artifacts /PRIVATE/browser-recovery --spec smoke.spec.cjs
```

Restore each of the four exact snapshots captured during Steady50. Default recovery builds from source; do not preload the old app image and describe that as a clean source-only recovery. Confirm all API routes, new backup health and real browser workflows. Independently compare every captured non-session SQL row—including password hashes—schema/FKs/revisions/authorship and every referenced blob, including Trash. Restored sessions must be absent before verifier/browser sign-ins. Separate newly created namespaced verifier rows from captured rows. Hash the original volume bytes again to prove recovery did not overwrite them. Retain original and recovery evidence before cleanup.

## Unknown outcomes, evidence and cleanup

A timed-out write/upload is not automatically replayed. Inspect confirmed IDs/status and the original attempt identity. Failed required checks, unrun cases, missing evidence or a narrowed subset cannot be called a full pass. Do not change the declared load/durations/budgets after seeing results. A materially changed app build starts a new source/image epoch and requires affected checks; controller-only prerequisite changes must still pass their own tests.

Retain private `lab.json`, storage state, source/image digests, raw workload/cgroup reports, capture IDs, screenshots, API logs and independent audit results. Passwords, cookies, CSRF values, private keys, raw databases and full credential-bearing artifacts stay outside Git; redact before sharing. The current [public summary](../../docs/evidence/v0.2.0-summary.json) is a sanitized index, not the private oracle.

The root runner owns cleanup in reverse order: stop owned clients, then selected backup workers and apps, then recovery/original daemons as intended, then authority/storage fixtures. Verify actual engine, resource ID/name and `io.opensw.test` label against saved state before each destructive removal. Preserve evidence first and retain data volumes when investigation requires it. There is no broad cleanup command in this guide: never use global Docker prune, delete unrelated resources, clear unknown repository locks or erase original data to make a run pass. Report cleanup separately; creating these instructions does not establish that cleanup has run.
