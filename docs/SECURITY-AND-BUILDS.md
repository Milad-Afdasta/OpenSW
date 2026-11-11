# Know what you are running

OpenSW builds each app from the public source checkout on the Docker host you choose. There is no required prebuilt OpenSW application package, hosted account, license server or paid AI API. Supporting Caddy and Restic components are fetched from explicitly pinned upstream container images.

## What the installer builds

1. It packages the public application source, excluding local configuration, credentials, dependency directories, and runtime data.
2. It builds the selected app using the repository's visible [Dockerfile](../Dockerfile).
3. The frontend uses `npm ci` and its committed lockfile. Python packages use exact versions and required hashes from `backend/requirements.txt`.
4. Node, Python and Restic images are pinned by immutable image digest. The optional Caddy gateway has its own pinned digest. Updating any digest is an explicit source change.
5. It records the source archive SHA-256 and starts the confirmed immutable Docker image ID. A clean Git checkout also records its commit; a modified/source-ZIP checkout records the actual source archive identity.
6. It preserves that installed image and the app's independent data volume on an ordinary rerun. Updating source does not silently replace a running installation.

Review a release tag and its Dockerfile before installation. A source build gives you control of the build input; it still depends on your Docker host, upstream base images, compilers, and dependencies.

The backup worker runs the same source-built app image as its app, with the pinned Restic binary available inside it. Only the selected app's migrations/schema/frontend are built into each variant. The gateway remains a separate infrastructure container; no OpenSW app requires another app to be installed.

## Signed packages and reproducibility

Prebuilt packages are a future convenience path, not a prerequisite or an unchecked fallback. They must not be called verified until their provenance verification is implemented and tested.

A sound prebuilt distribution should publish an artifact digest, the source commit, its public CI workflow, a signed build-provenance attestation, and a dependency inventory. The installer should reject a failed verification instead of loading the package. GitHub documents [artifact attestations and verification](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations).

Signed provenance establishes the artifact's build origin. It does not establish that every dependency is safe, or that an independent rebuild produces identical bytes. A [reproducible build](https://reproducible-builds.org/docs/definition/) requires an independently verified bit-for-bit match. OpenSW does not claim that without that evidence.

## Independent apps

Each app has its own container/process, user database, sessions, persistent volume, administrator bootstrap, hostname, and port. Installing Notes does not require Files, Projects, or Chat. Shared source utilities are reused during development; the applications do not share runtime accounts or data.

Default browser addresses use distinct `.localhost` names because cookies are not isolated by port alone. Session cookies are host-only, app/instance-specific, HttpOnly, and SameSite Strict. Browser writes also require the expected Origin and a session CSRF token.

Private HTTP binds only to loopback; existing-server access uses authenticated SSH and the printed tunnel. Optional public HTTPS uses the installer-managed Caddy gateway, a selected private app network and a real certificate for the configured DNS hostname or public IPv4 address. Applications are not directly published as public HTTP backends. A reachable public IPv4 address (one app per server) or DNS hostname, incoming challenge/HTTPS ports, an existing host and authorized encrypted storage are operator prerequisites; new cloud-server/domain/storage provisioning is not automated.

This isolation assumes a stable Docker daemon **28.0.0 or newer** with standard protected bridge/NAT/firewall behavior. The installer validates the selected daemon's `ServerVersion` before managed resource operations and rejects older, prerelease, malformed or missing versions. It does not substitute the client version or update host packages. Update target Docker without deleting existing volumes, then recheck; use a currently patched release. Docker documents that [pre-28 engines can expose localhost-published ports to the same L2 segment](https://docs.docker.com/engine/network/port-publishing/).

The version floor is not host attestation or general security certification. Intentional direct-routing/trusted-interface exposure, routed or unprotected gateway modes, disabled Docker firewall/raw-table protections, and host rules exposing private backends are unsupported. Docker's [28 release notes](https://docs.docker.com/engine/release-notes/28/) describe such protection overrides. Host/provider networking remains the trusted operator's responsibility; the installer adds no privileged networking probe, firewall mutation or attestation checkbox.

Public verification uses actual HTTPS with normal certificate, hostname and Secure-cookie behavior. Private CAs are explicit configuration inputs; verification is never bypassed. The gateway owns renewal, and each origin retains its own configured certificate issuer/trust policy. Public routing changes use a durable confirmed configuration; a failed final check after possible admission is reported as degraded rather than triggering a database rollback.

Upgrade/reconfiguration is explicit. The installer stops the selected backup worker before image/origin changes and preserves retained account/content identity. Origin changes revoke sessions. Ordinary install preserves the installed image. The supported retained database schema is checked against its canonical app schema; source or architecture-specific image provenance is not treated as interchangeable with data identity during recovery.

## Your data and credentials

- A hidden setup prompt creates the first administrator password. There is no reusable default password.
- The one-time bootstrap secret is removed after the database transaction commits. Reruns preserve existing users and passwords.
- Local wizard configurations are private and ignored by Git. SSH private keys are referenced by path and never copied into the image.
- Actual filled config, key and storage-secret paths, including inode aliases, are excluded from the build archive. Secrets must not appear in image build arguments, labels, published source or diagnostics.
- The app runs as a non-root user. Databases and uploaded content live in its own persistent volume.
- The independent backup worker uses UID 10001, read-only app data and a private operations/secrets volume. It has no Docker socket. Storage keys and the recovery password are private files; credential files accept only the documented literal AWS keys, with no shell evaluation.
- Restic encrypts snapshots in the configured HTTPS S3 repository. Each app/instance gets a dedicated repository suffix. Foreign snapshots/locks reject operations; there is no global automatic unlock. Retention runs only after a confirmed successful snapshot and its required verification, with at least one latest snapshot retained.
- A restore targets an empty recovery volume, verifies schema/identity/relative paths/hashes/referenced bytes, and clears restored sessions. The original data is not overwritten. Keep storage access and recovery-password information safely outside the original server.
- Trash, archives, and deleted-message placeholders retain records. They are not a backup or an automatic permanent-deletion policy.
- Anyone who controls the Docker host can access its application data. Use host access controls, encrypted backup storage, and a retention policy appropriate to your team.

The server bounds unauthenticated login hashing and rejects unauthorized Files uploads before multipart-body consumption. These controls reduce known resource pressure; they are not an uptime guarantee or a substitute for operating-system/provider controls and measured workload evidence. See [Validation](VALIDATION.md) for the exact tested epoch and remaining gates.

## Third-party components

| Component | Use and retained notices |
| --- | --- |
| OpenSW application source | [MIT](../LICENSE). |
| Locked frontend dependencies | Collected license texts in [third-party-notices.txt](../web/public/third-party-notices.txt), also served in the built frontend. |
| Locked Python dependencies | Original installed wheel/distribution license files retained in the app image. |
| Restic 0.19.1 | Unmodified binary copied from its pinned official image. Its exact upstream [BSD 2-Clause license](../operations/RESTIC-LICENSE.txt) is copied with `operations/` to `/app/operations/RESTIC-LICENSE.txt`. [Upstream version/source](https://github.com/restic/restic/tree/v0.19.1), [dependency manifest](https://github.com/restic/restic/blob/v0.19.1/go.mod). |
| Caddy 2.11.9 | Separate pinned official gateway image; [upstream source and Apache 2.0 license](https://github.com/caddyserver/caddy/tree/v2.11.9). |
| Node/Python/container operating-system packages | Their upstream images and installed packages retain their own license/copyright files. Exact pins live in the Dockerfile and installer/gateway source. |

This is a component/notice guide, not a complete operating-system or compiled-dependency SBOM. The source-only release fetches the pinned upstream components at build/setup time. Any future prebuilt distribution needs its own complete dependency inventory, notices and provenance evidence; the OpenSW MIT license does not replace upstream terms.

## Reporting a problem

Please do not include passwords, session cookies, private SSH keys, database archives, or private customer data in a public issue. Include the app, release/source identity, the failed verifier step, and redacted error output. The verifier and app guides explain how to collect the selected container's logs.
