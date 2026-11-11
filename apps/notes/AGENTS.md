# AI setup instructions: OpenSW Notes

These instructions apply to this independent app. Follow any applicable parent instructions first. Use the exact **OpenSW** capitalization. The app must have its own configuration, container, user accounts, database, storage, port, and browser origin; another OpenSW app is not a prerequisite.

## Set up for a nontechnical owner

1. Read this app's [README](README.md), [configuration example](config.example.toml), and the repository's [main README](../../README.md).
2. Collect only missing setup details: team name; this computer or an existing server; and, for a server, its address, login username, and SSH key **file path**. For public HTTPS also collect the public IPv4 address (one app/server) or owned DNS hostname, certificate email and existing authorized S3-compatible HTTPS bucket. Use hidden installer prompts/private files for storage keys and the backup recovery password. Never ask the user to paste private keys or passwords into chat. The optional advanced section is for deliberate instance, port, SSH-port or Files upload-limit choices.
3. Prefer the guided root entry point, `./install.sh`. For automation, copy this example to a private `*.local.toml` file, make it mode `0600`, and pass exactly one `--config`. Do not commit the filled file, server keys, passwords, cookies, or runtime data.
4. From the repository root, inspect the target before installation:

   ```sh
   ./install.sh plan --config path/to/notes.local.toml
   ```

5. Install that exact app/configuration:

   ```sh
   ./install.sh --config path/to/notes.local.toml
   ```

   The installer builds from the public source. It may pause for Docker installation/startup, SSH host-key verification, or unlocking an encrypted key through the user's SSH agent. Explain the exact action in plain language, then let the installer recheck it. Do not disable host-key checking, switch a global Docker context, change an unrelated server, or create paid cloud resources.
6. Use the readiness result and printed app address. A successful build/container command alone is not proof of a ready app. Private existing-server access uses the printed SSH tunnel. Public HTTPS requires both `[https]` and `[backups]`, reachable public IP or correct DNS, open TCP80/443, trusted certificate checks, a first verified encrypted backup and the complete API verifier. The installer manages certificate issuance/renewal and backup scheduling; it does not buy a domain, create a cloud machine or provision paid storage. Report app, HTTPS and backup verdicts separately.

## Operate and recover the selected installation

Use the [configuration guide](../../docs/CONFIGURATION.md) and [backup procedure](../../docs/BACKUP-AND-RECOVERY.md) as the command owners. Ordinary install preserves the installed image. Source/origin changes require explicit `upgrade` or `reconfigure` with the same private configuration; origin changes revoke sessions. Do not edit persisted runtime identity to force adoption.

```sh
./install.sh status --config path/to/notes.local.toml
./install.sh upgrade --config path/to/notes.local.toml
./install.sh backup --config path/to/notes.local.toml
```

Restore only onto a separate empty target with no existing selected app container/data volume:

```sh
./install.sh restore --config path/to/notes.local.toml --snapshot FULL_64_CHARACTER_SNAPSHOT_ID
```

Use the existing app administrator password for the hidden verification prompt; the backup recovery password is a different private file. Default recovery builds this selected source release, checks the exact supported app/schema/configuration and archive integrity, clears restored sessions, and runs readiness plus the full API verifier. A new explicitly configured HTTPS origin is allowed; other saved runtime values must match. Preserve the original host and data. Do not switch users to a partial recovery or claim a future operator's DNS/provider setup has been tested by a local fixture.

Inspect status after a timeout or unknown outcome; do not blindly repeat a write or clear repository locks. Never remove volumes, reseed accounts, delete referenced blobs or roll an older database over accepted writes to make a check pass. A post-admission verifier failure is degraded state to reconcile, not permission for data rollback.

## Verify the running app

Run the complete post-install API verifier with the same selected configuration:

```sh
./verify.sh --config path/to/notes.local.toml
```

Enter the existing app administrator credentials through the hidden prompt/private input supported by the wrapper. The verifier creates exactly two uniquely named temporary members and its own test records, exercises **page → summary/detail → Markdown edit/save → stale revision rejection → archive/restore**, and reports named `PASS`, `FAIL`, or `BLOCKED` results plus route coverage. A nonzero exit or unrun route is not a pass.

The verifier must not change an existing operator's password, account status, settings, or content. Password and deactivation tests apply only to confirmed temporary member IDs with matching usernames/roles. Do not guess IDs after a timeout or blindly repeat a write with an unknown outcome.

Cleanup is intentionally soft. Archived pages and authored identities stay in this app's database. Report the verification namespace and any cleanup failure to the owner; never hide retained test data or delete a volume to make a check pass.

For optional local development checks, first follow the canonical [fresh environment setup](../../docs/API.md#development-checks) from the repository root to create `.venv` and install the committed, hash-locked test dependencies. Then run the API invariants:

```sh
PYTHONPATH=backend .venv/bin/python -m pytest -q tests backend/tests
```

The optional browser harness lives in [tests/browser](../../tests/browser). It requires development-only Playwright/Chromium; those are not bundled into the small production image. A browser check must sign in and perform actual actions, verify server-confirmed data, and inspect both light/dark desktop/mobile layouts. Screenshots alone are not a functionality pass.

## Diagnose without guessing

The canonical [API guide](../../docs/API.md) contains private-cookie/CSRF curl examples, exact route coverage, and diagnosis. Reuse that guide rather than inventing a second authentication or API contract.

- Authentication failures: check the selected app, exact account, active state, and supplied credentials; accounts are independent across apps.
- `403`: check the configured origin, CSRF token, role, and—where relevant—conversation membership.
- `404`: check app/resource identity; another app's routes are intentionally unavailable.
- `409`: inspect the confirmed state or revision before another write; preserve unsaved input.
- Validation/size failures: check the named field or configured upload limit.
- Storage/server failure: inspect only this deployment's status and logs. Use `./install.sh status --config path/to/notes.local.toml` and the exact target-specific diagnostic command printed by the wrapper. Do not clear data or regenerate credentials.

A revision conflict must preserve the unsaved text. Never replace a user's draft or silently overwrite a newer saved page. A plain Markdown editor is the intended first-release format; do not promise block databases or co-editing.

Retain the tested source commit/image identity, selected app/configuration, actual results, and relevant redacted evidence. Never report a live cloud deployment, verified binary provenance, or complete QA without directly observing it.
