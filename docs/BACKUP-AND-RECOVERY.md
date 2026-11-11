# Encrypted backups and recovery

Each configured app has an independent scheduled backup worker. It captures an online SQLite snapshot, the saved runtime configuration, and, for Files, exactly the immutable UUID blobs referenced by that frozen database, including Trash. The encrypted off-host repository belongs to that one app/instance. The app stays available during scheduled captures.

Trash, archives and message tombstones are retained application data, not backups. Operators still own storage access, available disk space, monitoring, retention requirements, recovery-password custody and recovery rehearsals.

## Configure storage

Use the guided `./install.sh` wizard, or add this to the selected app's private TOML:

```toml
[backups]
repository = "s3:https://storage.example.com/my-backup-bucket"
credentials_file = "/private/storage-credentials.env"
password_file = "/private/recovery-password.txt"
every_hours = 6
retain_days = 30
verify_every_days = 7
# ca_file = "/private/storage-root-ca.pem"
```

The bucket must already be authorized; setup does not create a paid provider account. The installer appends the deployment identity, for example `/opensw-files-team`, to the configured base. Do not share that effective repository with other applications, tools or instances. Configure a provider policy that permits this prefix's required read/list/write/delete operations. A storage lifecycle rule must not independently remove Restic's config, keys or retained objects.

The private credentials file uses literal `AWS_ACCESS_KEY_ID=...` and `AWS_SECRET_ACCESS_KEY=...` lines, optionally `AWS_SESSION_TOKEN`, `AWS_DEFAULT_REGION` or `AWS_REGION`. It is never sourced as shell code. Keep it and the password file mode `0600`. The optional CA file establishes explicit trust in a private storage CA; HTTPS verification remains enabled. See [configuration](CONFIGURATION.md) for the complete input contract.

Keep a separate safe copy of the recovery password, storage credentials/access instructions and the app's private TOML. Losing the password makes encrypted snapshots unrecoverable. Store that recovery information outside the app host so a lost server does not also lose its recovery material.

Budget local working space as well as remote storage. A full verification currently downloads an uncompressed archive, extracts a validated staging copy, and creates the recovered copy. Allow approximately **three times the selected snapshot's uncompressed size in free working space**, in addition to live app data and Docker images. Add the requirements of apps that verify concurrently. Files includes Trash. An off-server bucket does not provide this local working space. Initial setup and periodic full verification fail if that space is unavailable; monitor free disk space and size the host before filling it with files.

Run setup with the same configuration to install/configure its worker. Public HTTPS setup waits for the first encrypted snapshot and a full authenticated restore check before readiness. Backups can also be enabled for a private HTTP deployment.

## Check health or make a manual capture

```sh
./install.sh status --config files.local.toml
./install.sh backup --config files.local.toml
```

Status reports heartbeat, latest attempt/error, latest confirmed snapshot, and last full restore-verification time. Preserve the complete `last_success.snapshot` value for recovery. A backup failure, overdue success/verification or stale heartbeat returns a failing health verdict. A fresh verified backup remains healthy while its next scheduled capture is in progress. `stop` stops the worker as well as its app; rerunning install with the same config restarts them.

One exclusive per-app lock covers scheduled/manual backup and restore work. A concurrent manual request is rejected instead of overlapping. Failed or interrupted uploads are not automatically replayed. An interrupted run is reconciled by its unique tag; a later scheduled attempt is a new capture. After a timeout, inspect the same target's status and repository before issuing another command. Repository locks are never globally unlocked or silently deleted; confirm their owner and liveness before taking a separate recovery action.

The worker checks repository metadata after ordinary backups and performs a complete authenticated download/restore validation on the first backup and every seven days by default. Configure `verify_every_days` to choose another interval. A successful restore check verifies data integrity; it does not replace a rehearsal of host loss, credential recovery, DNS and the real application workflows.

Retention runs only after a new confirmed snapshot passes its required verification. It keeps snapshots within `retain_days` plus at least one latest snapshot. Snapshots are grouped by stable host/path identity; unique run tags do not create separate retention groups. Any foreign snapshot or repository lock fails closed. Previous successful snapshots are preserved after failed uploads; no failed backup authorizes pruning.

## Restore onto an empty target

Use a separate recovery host/engine with **no existing container or data volume for this app/instance**. Keep the original host and volume intact. The same app, instance, team and upload setting must match the saved runtime configuration. The target/server login may point to the new machine. An explicitly configured new HTTPS origin is allowed; other runtime identity/config changes are rejected.

1. Download this source release and make the original private TOML, storage credentials and recovery-password file available on the recovery computer. Update only the intended target and, if needed, the explicitly selected HTTPS origin. Confirm DNS/ports and storage permissions.
2. Choose the complete confirmed snapshot ID from backup status. Use the existing application administrator's password; it is separate from the backup recovery password.
3. From the source directory, run:

   ```sh
   ./install.sh restore --config files.local.toml --snapshot FULL_64_CHARACTER_SNAPSHOT_ID
   ```

   The existing administrator password is prompted privately. For automation, add `--password-file /private/existing-admin-password.txt --non-interactive`; use `--admin-username` if it was not `admin`.
4. Wait for the complete result. Recovery builds the current selected app from source by default, checks the supported exact schema and archive, revokes restored sessions, starts the app, runs readiness and the full app API verifier, then configures its independent backup worker. An advanced `--image-id sha256:...` may select a validated already loaded image. Historical snapshot image/source IDs remain recorded provenance; architecture-specific image equality is not required.
5. Inspect your actual team records and, for Files, representative downloaded bytes. Sign in again; restored sessions have deliberately been cleared. Confirm fresh backup/HTTPS health and complete the real workflow checks before switching users.

Unsafe archive paths, links, duplicate entries, checksum failures, missing referenced blobs, foreign app/instance, unsupported schema or configuration mismatch reject recovery. A failed destination is retained for diagnosis and must not be used as a successful restore. The command does not overwrite the original volume. Do not delete a failed destination merely to repeat an operation with an unknown outcome; inspect its confirmed state first.

For HTTPS, the proxy may admit traffic before the final API verifier finishes. A later failure means degraded recovery to inspect; it never authorizes restoring an older database over possible accepted writes. There is no automatic data rollback after admission.

## What a capture proves

SQLite's online backup API creates the database snapshot before referenced blobs are streamed. Schema/ownership metadata, integrity and foreign keys are checked. The archive contains relative paths and a checksum/provenance manifest. Saved configuration bytes must remain stable across capture; missing, replaced, linked or changed blob data fails the producer. Restic invokes the producer with `--stdin-from-command`; it does not accept an unchecked shell pipe as evidence of a completed snapshot.

The worker runs as UID 10001 with read-only app data, its own private operations volume and no Docker socket. Its password and storage keys are private files rather than command arguments. Installer transitions confirm the worker stopped before app image/origin changes. These boundaries preserve app identity and snapshot provenance without scheduled downtime.

The [validation record](VALIDATION.md) separates focused invariants, controlled real HTTPS/S3 protocol checks, all-app recovery/API checks and workload results. Earlier stopped-volume Files recovery evidence applies to that historical release; it is not proof of the current scheduled encrypted flow or a future operator's provider configuration.
