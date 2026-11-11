# Put OpenSW on DigitalOcean using an IP address

Choose one app and one Ubuntu server. You can open the app at `https://YOUR_SERVER_IP`; you do not need to buy a domain. Four apps on bare IP addresses need four servers/IPs. If you later want to share one server, use a different DNS hostname for each app.

This guide uses Ubuntu 24.04, Docker's official packages, an SSH key and the ordinary OpenSW source installer. Public setup also needs an authorized **off-server S3-compatible HTTPS backup bucket**. The installer encrypts backups with a separate password and verifies an initial restore before reporting public readiness.

## 1. Create the server

In DigitalOcean, create a Droplet with:

| Setting | Value |
| --- | --- |
| Image | Ubuntu 24.04 LTS, x64 |
| Plan | Basic, 2 vCPU / 4 GiB RAM / 80 GB SSD for the tested small installation |
| Region | Near your users; the validation uses New York 3 |
| Authentication | Your SSH public key; keep the private key on your computer |
| Name | Something recognizable, such as `opensw-files` |

Record its ID and public IPv4. Choose disk capacity for your actual files, images and backup working space; this small validation does not establish a capacity limit. See the [backup space requirements](BACKUP-AND-RECOVERY.md).

Create a DigitalOcean Cloud Firewall and attach it to that Droplet:

| Direction | Protocol/port | Source/destination |
| --- | --- | --- |
| Incoming | TCP 22 | Your computer's current public IPv4, with `/32` |
| Incoming | TCP 80 | All IPv4 addresses; certificate validation and HTTPS redirect |
| Incoming | TCP 443 | All IPv4 addresses; app and certificate validation |
| Outgoing | Normal TCP/UDP/ICMP | Allow ordinary package, certificate, DNS and storage access |

Keep Docker's standard protected bridge/NAT/firewall settings. Do not open application ports 8081–8084, port 8000, Docker 2375/2376, or Caddy's admin port 2019. A provider firewall protects the host independently of Docker's published ports.

## 2. Verify SSH and install Docker

Use DigitalOcean's trusted console to run:

```sh
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

On your own computer, connect with your actual key path and server IP:

```sh
ssh -i /private/path/to/your-key root@YOUR_SERVER_IP
```

Compare the displayed Ed25519 fingerprint with the console result before accepting it. Do not disable host-key checking. If the fingerprint differs, stop and check which server you selected.

If your private key is protected by a passphrase, unlock it in your local SSH agent before running the installer (`ssh-add /private/path/to/your-key`). The installer uses noninteractive SSH and cannot ask for a key passphrase.

In that SSH session, install Python and Docker from Docker's signed Ubuntu package repository:

```sh
sudo apt-get update
sudo apt-get install -y ca-certificates curl python3
sudo install -m0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
  "$VERSION_CODENAME" "$(dpkg --print-architecture)" | \
  sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
python3 --version
sudo docker info --format '{{.ServerVersion}}'
exit
```

OpenSW requires Python 3.11+ and a stable Docker server 28.0.0+; use the currently patched Docker package. These commands use a fresh Ubuntu image. Follow [Docker's official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/) when modifying an existing server.

## 3. Download OpenSW on your computer

Your controlling computer needs Python 3.11+, an OpenSSH client and Docker CLI. macOS can use Docker Desktop. Run the source installer from your computer; it builds on the selected server over authenticated SSH. It does not send your private SSH key to the server.

Use **OpenSW v0.2.1 or newer** for public-IP HTTPS. Download the source ZIP and its checksum file from the same [OpenSW release](https://github.com/Milad-Afdasta/OpenSW/releases). Version 0.2.0 requires a DNS hostname for successful public HTTPS. Download `OpenSW-v0.2.1.zip.sha256` alongside `OpenSW-v0.2.1.zip`. In the folder containing both downloads, verify the archive:

```sh
# macOS
shasum -a 256 -c OpenSW-v0.2.1.zip.sha256
# Linux / WSL2: use this instead
sha256sum -c OpenSW-v0.2.1.zip.sha256
```

Require `OpenSW-v0.2.1.zip: OK`. Then extract the ZIP and open a terminal in the extracted folder. For a newer release, use its matching filenames. See [source and build verification](SECURITY-AND-BUILDS.md).

For guided setup:

```sh
sh install.sh
```

Choose your app, **An existing server**, its IP, login `root`, and your SSH key file path. Enable public HTTPS/backups; enter the same public IP as the HTTPS address, a certificate contact email and your existing backup details. Secret prompts are hidden. The wizard saves a private configuration you can review and edit.

For an editable configuration instead, copy the selected example:

```sh
cp apps/files/config.example.toml files.local.toml
chmod 600 files.local.toml
```

Fill these values, and enable the two optional sections already shown in the example:

```toml
app = "files"
team_name = "My team"
run_on = "my-server"
server_address = "YOUR_SERVER_IP"
server_username = "root"
server_key_file = "/private/path/to/your-key"

[https]
domain = "YOUR_SERVER_IP"
email = "you@example.com"

[backups]
repository = "s3:https://YOUR_STORAGE_HOST/YOUR_BUCKET"
credentials_file = "/private/storage-credentials.env"
password_file = "/private/backup-recovery-password.txt"
```

The name `domain` is also used for an IPv4 address. Enter the bare address, without `https://` or a port. For Projects, Notes or Chat, use that app's example and `app` value. Accounts and data are independent.

Your private storage credentials file contains literal unquoted lines:

```text
AWS_ACCESS_KEY_ID=YOUR_STORAGE_ACCESS_KEY
AWS_SECRET_ACCESS_KEY=YOUR_STORAGE_SECRET_KEY
```

Include optional `AWS_DEFAULT_REGION` only when your storage provider requires a region; see the [credential format](CONFIGURATION.md). The test storage used region `garage`.

The other file holds your strong backup recovery password. Make both files private with `chmod 600`, and keep an independent safe copy of the recovery password. Use storage credentials authorized to list/read/write/delete the dedicated backup prefix. OpenSW adds a separate prefix for each app/instance. An HTTPS S3 service is required; a backup directory on the app server does not protect against losing that server.

## 4. Install and check the result

```sh
sh install.sh plan --config files.local.toml
sh install.sh --config files.local.toml
sh install.sh status --config files.local.toml
sh verify.sh --config files.local.toml
```

The installer asks privately for the app administrator password. It builds from source, initializes the app, obtains a trusted IP certificate, creates an encrypted off-server backup, validates a full restore, and exercises the entire app API through HTTPS. Wait for the final ready result, rather than treating a running container as completion.

Open the printed `https://YOUR_SERVER_IP` address and sign in. No certificate-warning bypass is needed. For IP certificates, the gateway automatically requests Let's Encrypt's `shortlived` profile and handles browsers that omit SNI for IP addresses. Certificates last 160 hours; the server must stay online with automated renewal and certificate-validation ports reachable. [Let's Encrypt profiles](https://letsencrypt.org/docs/profiles/)

Optional automated setup can use `--password-file /private/admin-password.txt --non-interactive`. This password is separate from the backup recovery password. Do not put either password in a command argument or Git.

## 5. Confirm everyday use and retained data

Try the app's actual workflow: upload/download a file; create and update a task; edit a note; or send a message. Refresh and sign in from another browser to confirm saved data. The [browser harness](../tests/browser/README.md) can run these workflows across desktop/mobile sizes in light and dark mode.

To stop and start the same installation:

```sh
sh install.sh stop --config files.local.toml
sh install.sh --config files.local.toml
sh install.sh status --config files.local.toml
```

Ordinary install preserves the installed image, accounts and data. Use `upgrade` explicitly when updating source. Check backup health and rehearse [empty-server recovery](BACKUP-AND-RECOVERY.md#restore-onto-an-empty-target). When the original host is available, stop its app and backup worker before giving a recovery server the same backup repository. Recovery can explicitly use the new server's public IP; restored users must sign in again.

## Troubleshooting

| Result | Check |
| --- | --- |
| SSH rejected | Correct IP/key/login, matching trusted host key, unlocked SSH agent for a passphrase-protected key, firewall source equal to your current public IP |
| Docker prerequisite rejected | Actual remote Docker server version and SSH account's Docker access |
| Certificate not ready | Same public IP in config, ports80/443 reachable, gateway logs and actual ACME rejection; do not bypass TLS |
| `WRONG_VERSION_NUMBER` despite a valid certificate on the server | Compare from a separate authorized network and inspect network filtering/proxy responses. In this validation one newly assigned IP received a SafeBrowse redirect on the controller network; its unused test server was replaced once with a fresh IP. Keep TLS checks enabled; do not treat a filter page as app readiness |
| Backup failed | Storage endpoint, dedicated-prefix permissions, private credentials/password paths, free working space; keep original data |
| Sign-in temporarily limited | Actual credentials and the one-minute rate window; do not weaken authentication |
| A failed old v0.2.0 IP attempt is rejected | Preserve its data/config for diagnosis. Unsupported old IP gateway policy is not automatically adopted or repaired. Recover any confirmed backup onto an empty server; do not delete an existing data volume to force setup |

Use `sh install.sh logs --config files.local.toml` and `status` to inspect a failed or timed-out operation before another write. Keep reports and credentials private. Full diagnostics and endpoint checks are in the [API guide](API.md).

## Destroy temporary test servers only

Export results first and retain any backup material you need. Deleting the Droplet permanently loses its local data. In DigitalOcean, select the exact temporary Droplet ID/name you recorded and choose **Destroy**. Powering off a Droplet continues to incur charges. Remove only test-owned firewall/key/tag resources and any separately created storage; leave production backup storage intact.

After deletion, verify that each recorded temporary ID is absent in DigitalOcean. Associated block volumes, snapshots or provider backups can survive Droplet destruction, so check any you deliberately created. This validation creates none. [DigitalOcean destruction behavior](https://docs.digitalocean.com/products/droplets/how-to/destroy/)

## Validation record

Validated on **9 November 2026** with four independent Ubuntu 24.04 x64 app Droplets in DigitalOcean NYC3, each 2 vCPU / 4 GiB / 80 GB, Docker 29.9.3, and separate temporary Garage S3-compatible storage. All origins used bare public IPv4 addresses and publicly trusted Let's Encrypt certificates. No DNS aliases, private-CA imports or certificate-warning bypasses were used.

| App | Initial install | API routes | Browser cases | Exact recovered rows | Blob hashes | Old sessions revoked | Restore command |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Files | 398.7s | 16/16 | 8 | 33 | 11 | 17 | 223.2s |
| Projects | 156.5s | 18/18 | 28 | 881 | 0 | 18 | 168.0s |
| Notes | 160.8s | 12/12 | 8 | 19 | 0 | 14 | 176.8s |
| Chat | 150.2s | 18/18 | 28 | 542 | 0 | 20 | 165.8s |

Times measure the actual installer/restore commands after SSH/Docker prerequisites were ready; they include source builds, HTTPS, backup/restore validation and the canonical API checks. Droplet creation and OS/Docker bootstrap are additional (roughly three minutes in this run). These are observed setup times, not a completion-time or capacity guarantee.

All four source installations survived stop/install with the same container and image and exact retained records. Each captured snapshot was then restored onto a genuinely empty independent server/engine. Every original non-session database row and referenced blob was compared with its source; schema, integrity and foreign keys passed. Across all apps: **1,475 exact rows, 11 blobs and 69 captured sessions revoked**. The Files audit includes nine trashed files. Explicit old-cookie requests—not an empty cookie jar on a new IP—returned 401; fresh member sign-ins and Chat nonparticipant rejection passed.

Initial API coverage was 64/64 required routes and 146 assertions. The browser catalogue accepted 72 unique cases: 71 passed initially, and one Chat pagination-helper failure passed after its confirmed-DOM wait was corrected without weakening product assertions. The original failure is retained in the record. Fresh recoveries passed 64/64 routes, 146 assertions and 16 browser cases. Three independent reviewers inspected 16 populated desktop/mobile light/dark views; no blocking visual defect was observed.

One Files certificate required existing Caddy/ACME retries after protocol failures; its unchanged client succeeded without a manual order or restart. One initially assigned Chat recovery IP verified normal TLS on its own server but received a SafeBrowse redirect on the controller network. Before any app restore, the wait was cancelled, no app/data/locks were confirmed, and that unused host was destroyed. A single separately recorded new-IP host completed the unchanged checks. TLS and network protection stayed enabled. The filter's internal decision is unknown.

**All 10 temporary Droplets were destroyed**, including the unused filtered host, and all 10 provider GETs returned 404. The task-owned firewall, SSH key and tag were also removed; baseline DigitalOcean resource IDs remain. No provider backups, snapshots, block volumes, reserved IPs or Spaces subscription were created. The historical addresses in the evidence are released; do not probe them.

This is a real setup/recovery test, not a DigitalOcean concurrent-user capacity benchmark. Natural 160-hour IP certificate renewal was not observed during this short test. Earlier controlled renewal and workload evidence remains separately scoped in the [validation record](VALIDATION.md). Chromium mobile-sized views do not establish physical-device/Safari certification. The temporary Garage service tests HTTPS S3 compatibility, not DigitalOcean Spaces-specific permissions.

The [machine-readable record](evidence/v0.2.1-digitalocean.json) binds source hashes, image/engine identities, route counts, snapshots, semantic audits, measured durations and exact resource cleanup.
