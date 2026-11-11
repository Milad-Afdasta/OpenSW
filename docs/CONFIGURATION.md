# One short configuration per app

Run `./install.sh` and answer the setup questions. The wizard saves a private `<app>.local.toml` file. It can collect public HTTPS and encrypted storage settings through ordinary questions and hidden secret prompts. You can also copy an app's commented `config.example.toml`, make the copy private, and edit it:

```sh
cp apps/files/config.example.toml files.local.toml
chmod 600 files.local.toml
./install.sh plan --config files.local.toml
./install.sh --config files.local.toml
```

The original six basic fields remain valid for private use:

```toml
app = "files"
team_name = "My team"
run_on = "this-computer"
server_address = ""
server_username = ""
server_key_file = ""
```

| Field | What to enter |
| --- | --- |
| `app` | `files`, `projects`, `notes`, or `chat`. |
| `team_name` | The name displayed inside this installation. |
| `run_on` | `this-computer`, or `my-server` for an existing Linux server. |
| `server_address` | Server only: IP address, hostname or SSH alias, without `https://`. IPv6 literal SSH addresses are not supported. |
| `server_username` | Server only: its SSH login, such as `ubuntu`. |
| `server_key_file` | Server only: private key **path**; blank uses your SSH agent. Never put key contents here. |

Each configuration owns one app/instance. Apps may share a machine while keeping separate accounts, sessions, containers and data volumes. Private addresses use distinct `.localhost` names and ports. Remote private use requires the SSH tunnel printed by setup.

The selected Docker daemon must report a stable Engine version of at least **28.0.0**. Setup reads `docker info`'s `ServerVersion`; the local client version cannot substitute for an older or unknown remote daemon. Prerelease, malformed and missing versions reject before managed resource operations. Update Docker on that target without deleting its volumes; interactive setup pauses once and genuinely checks the daemon again. A currently patched release is recommended. The minimum addresses Docker's documented [pre-28 localhost-publishing exposure](https://docs.docker.com/engine/network/port-publishing/), not every host-security concern.

Use standard protected bridge/NAT/firewall isolation. Intentional direct routing, trusted interfaces, routed/unprotected gateway modes, disabled firewall/raw-table protections or host rules exposing private application ports are outside the supported host configuration. The installer does not probe, change or attest the host firewall; OS and provider networking remain the operator's responsibility.

## Public HTTPS and encrypted backups

You need an existing Docker host, a public IPv4 address or a domain whose DNS points to it, incoming TCP 80/443, and an authorized S3-compatible HTTPS bucket. The computer running setup/verification must reach the configured HTTPS address (and resolve it when using DNS). OpenSW does not purchase domains, create servers or provision paid storage.

Enable both sections in that app's private TOML:

```toml
[https]
domain = "files.example.com"
email = "admin@example.com"

[backups]
repository = "s3:https://storage.example.com/my-backup-bucket"
credentials_file = "/private/storage-credentials.env"
password_file = "/private/recovery-password.txt"
```

The `domain` field accepts a lowercase DNS hostname or canonical public IPv4 address, with no scheme, path, wildcard or port. For an IP address, use one app/server: browsers omit SNI for IP URLs, so the gateway allows only one distinct IPv4 subject. Caddy requests the Let’s Encrypt `shortlived` profile automatically; IP certificates last about six days and require continuous automated renewal. A custom ACME service must support that profile/IP issuance. DNS-only settings are unchanged. See the [tested DigitalOcean instructions](DIGITALOCEAN.md). The certificate email is a certificate contact. Caddy obtains and renews the certificate. Public readiness also requires a successful encrypted backup, authenticated restore validation and the full API verifier over trusted HTTPS. HTTPS setup without `[backups]` is rejected. A private installation may enable backups without HTTPS.

The storage credentials file is private (`chmod 600`) and contains literal, unquoted entries:

```text
AWS_ACCESS_KEY_ID=YOUR_STORAGE_ACCESS_KEY
AWS_SECRET_ACCESS_KEY=YOUR_STORAGE_SECRET_KEY
```

Optional `AWS_SESSION_TOKEN`, `AWS_DEFAULT_REGION` and `AWS_REGION` entries are accepted. No other keys, duplicates, `export` statements or shell evaluation are accepted. Use provider keys authorized for the dedicated backup prefix, including listing, reading, writing and retention deletion. The recovery-password file is a separate private plain-text file containing your chosen strong password. Keep an independent safe copy of that password and the storage access information: a backup cannot be decrypted without its password.

Paths resolve relative to the TOML file when they are not absolute. The wizard keeps its private secret files in the ignored `secrets/` directory. Actual configuration, SSH-key and storage-secret input paths are excluded from source builds. Never commit filled configuration, secret files or backup data.

## Optional operational settings

The defaults are normally sufficient. Add these keys to your existing `[backups]` section; TOML permits that section only once:

```toml
every_hours = 6
retain_days = 30
verify_every_days = 7
# ca_file = "/private/storage-root-ca.pem"
```

Each app/instance automatically gets its own repository suffix, such as `/opensw-files-team`. A full restore check runs on the first backup and at least every `verify_every_days`; intervening backups run repository metadata checks. Retention follows a confirmed successful backup and always keeps at least one latest snapshot. See [backup and recovery](BACKUP-AND-RECOVERY.md) for failure and recovery behavior.

Advanced HTTPS settings are `acme_server` (an HTTPS ACME directory), `ca_file` (an explicitly trusted certificate authority), `http_port` (default 80), `port` (default 443), and `dns_resolvers` (IP addresses using DNS port 53). A shared host gateway must use consistent listener ports and DNS resolvers across its apps. A private CA file must establish the intended trust; verification is never disabled. Storage has its own separate optional `backups.ca_file`.

Other advanced choices remain optional:

```toml
[advanced]
instance_name = "team"
port = 8081
ssh_port = 22
max_upload_mb = 25 # Files only
```

| Option | Default | Purpose |
| --- | --- | --- |
| `instance_name` | `team` | A separate installation identity for another instance of the same app. |
| `port` | Files 8081; Projects 8082; Notes 8083; Chat 8084 | Private loopback browser port. |
| `ssh_port` | 22 | The existing server's SSH port. |
| `max_upload_mb` | 25 MiB, Files only | Maximum individual upload; does not create disk space. |

Unknown sections/fields and invalid values reject before installation. Changing the app, instance, team or upload configuration does not silently adopt retained data.

## Commands and retained installations

| Command | Result |
| --- | --- |
| `./install.sh plan --config files.local.toml` | Validate and explain the config without contacting Docker or creating resources. |
| `./install.sh --config files.local.toml` | Build a new selected app, or preserve/restart its existing installed image and data. |
| `./install.sh upgrade --config files.local.toml` | Explicitly build this source and transition the selected retained app. |
| `./install.sh reconfigure --config files.local.toml` | The same checked transition, for an explicit origin/configuration operation. |
| `./install.sh status --config files.local.toml` | Report app, HTTPS and backup state; failed checks return nonzero. |
| `./install.sh backup --config files.local.toml` | Request one new encrypted snapshot using the worker's exclusive lock. |
| `./install.sh logs --config files.local.toml` | Read the selected app's recent logs. |
| `./install.sh stop --config files.local.toml` | Stop the selected backup worker and app; preserve their data. |

To promote private HTTP to public HTTPS, add the two sections and run `upgrade`. Teammates must sign in again after an origin change because existing sessions are revoked. The installer stops the backup worker before changing app image/origin, checks the selected app and restarts its worker. It does not restore an old database over accepted writes. After possible public admission, a failed check reports a degraded deployment for inspection.

Recovery uses `restore` on a new empty target; see its [exact procedure](BACKUP-AND-RECOVERY.md#restore-onto-an-empty-target). `--image-id` is an advanced optional immutable-image override, not a recovery prerequisite. `--password-file` provides a private administrator password file for noninteractive setup/verification. `--admin-username` defaults to `admin`; `--non-interactive` rejects missing prerequisites rather than waiting for input. `--wait-timeout` is 10–600 seconds, default 600; a timeout is an unknown outcome to inspect, not permission to repeat a write.

If Docker, SSH authentication or host-key verification needs attention, interactive setup explains the manual action and rechecks it once. Pressing Enter alone cannot make a prerequisite pass. Linux, macOS with Linux-container Docker, and Windows through WSL2 are supported setup environments. Remote Linux hosts need Docker and an SSH account authorized to use it.
