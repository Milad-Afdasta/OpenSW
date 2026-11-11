# Validation record

## v0.2.1: real DigitalOcean public-IP setup and recovery

The deployment owner/scenario model is **OPENSW-DO-R3** in the [DigitalOcean model](DIGITALOCEAN-MODEL.md).

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

This is a real setup/recovery test, not a DigitalOcean concurrent-user capacity benchmark. Natural 160-hour IP certificate renewal was not observed during this short test. Earlier controlled renewal and workload evidence remains separately scoped [below](#earlier-controlled-validation). Chromium mobile-sized views do not establish physical-device/Safari certification. The temporary Garage service tests HTTPS S3 compatibility, not DigitalOcean Spaces-specific permissions.

The [machine-readable record](evidence/v0.2.1-digitalocean.json) binds source hashes, image/engine identities, route counts, snapshots, semantic audits, measured durations and exact resource cleanup.

### Instruction improvement

Classification: **execution miss** for the browser helper consuming a response before confirmed DOM state, and private fixture/readiness/CLI-response assumptions. The existing owner/confirmation and testing instructions already required those checks. Enforcement is the corrected DOM-append wait, real route/row/blob/session assertions, active-before-network readiness check, serial resource ledger, structured provider-404 checks and independent exact-ID cleanup audit. No global instruction edit was warranted; the app AGENTS changes document the requested product setup.

## Earlier controlled validation

## v0.2.0 production evidence

The v0.2.0 owner/scenario contract is **OPENSW-PRODUCTION-R13** in [MODEL CLOSURE](MODEL-CLOSURE.md). The measured workload remains the approved, immutable **R11** profile; R12/R13 narrow the supported Docker prerequisite and do not relabel or replay that workload. The app source archive SHA-256 is `ee6d57740ea5d10735d18f7ac38bab7ce276132b65c7aa1a61461c89878b08d2`, epoch `production-r11-20261108-2`. Tests ran on Docker **28.3.2** with standard protected bridge/NAT/firewall isolation. A version check does not attest administrator firewall changes or intentional direct-routing/security overrides.

The [public machine summary](evidence/v0.2.0-summary.json) carries the sanitized release evidence. [Reproduction instructions](../tests/production/README.md) distinguish test infrastructure, app checks, workload measurement and recovery. Private credentials, complete raw reports, password hashes and fixture data are not published with that summary.

### Tested images and retained data

| App | Original measured immutable image ID |
| --- | --- |
| Files | `sha256:882234778e4ce1fa5f8bb5771c68c9bac2ee091bcf34a77fc390f82e218e3077` |
| Projects | `sha256:1490d0a2252bd3227bc7203fa5b962b7afb712e3917f2196bd426efd52f16a45` |
| Notes | `sha256:1ba8122a49a0ab813b73a1cf13bf84b76a4961e1a0a8382d0d5bf7df3bd9ca37` |
| Chat | `sha256:868568c1a935233fdbcbe86603f5a13aed628edf56cea5489dcee616335daf78` |

Actual v0.1.0 installations were upgraded through the selected SSH/Docker target, preserving historical records, password hashes, revisions, schema and Files bytes, with deliberate session revocation on origin change. Current API outputs were checked against the actual frozen release contracts. Sixteen real v0.1.0 static-browser cases also pass against the new API; they are not recreated client mocks.

### Runtime and interface checks

| Gate | Current evidence and scope |
| --- | --- |
| Current real-browser suite | **72 PASS**, all four apps; default/dark desktop/mobile, actual actions and persisted-state assertions. |
| Released v0.1.0 browser | **16 PASS** against current APIs, using actual frozen static bundles. |
| Independent manual QA/review | Three new independent reviewers each completed 16 all-app viewport/theme checks plus four affected Projects views. Projects' changed frontend was rechecked in the final source epoch; the other three complete runtime-content hashes were unchanged. |
| HTTPS/ACME | All four apps passed trusted hostname/certificate/Secure-cookie verification, real private-CA ACME issuance and automatic renewal. Gateway restart, durable configuration preservation, rejected-load/final-publication fault handling, nonzero degraded status and policy-drift checks passed. |
| Automated invariants | Final complete suite: **282 PASS**. Includes authentication/body admission, historical contracts/data, schema equivalence, backup/restore failures, gateway durability, supported/old/unknown/prerelease daemon versions, no mutation on rejection, and a real prerequisite pause/recheck. Actual outer/recovery 28.3.2 targets passed the guard; the original engine was intentionally stopped after its prior version/runtime evidence was recorded. |

Initial harness and model failures remain recorded in MODEL CLOSURE and private evidence. A rejected Caddy load, an unestablished browser precondition or an unknown write was not relabelled as a pass. Corrections used new affected checks and, where app bytes changed, a new source/image epoch.

### Fixed concurrent workload

The full run made **35,068 operations**, including **20,433 measured-stage operations**, with no unexpected responses, unconfirmed mutations, app/container restarts or OOM events. Expected conflict/privacy rejections and deliberate login-abuse rejections were classified separately. Every actor used a persistent HTTP client; sign-ins were staged rather than repeatedly reauthenticated for each request.

| Stage | Actors per app | Fixed duration |
| --- | ---: | ---: |
| Ramp | 10 | 30 seconds |
| Ramp | 25 | 30 seconds |
| Steady | 50 | 300 seconds |
| Burst | 100 | 30 seconds |
| Recovery | 10 | 45 seconds |

All four apps ran concurrently. The run-owned starting dataset contained 200 real 1 MiB files, 501 tasks with 1 KiB descriptions, 501 notes with 4 KiB bodies and 10,000 messages. The confirmed final workload dataset contained 605 real 1 MiB files and 10,755 messages, with **16,585 confirmed mutation versions** retained in the private oracle. Files metadata also includes its folder; a metadata-row count is not a count of blob files.

| Metric | Worst measured stage or stated scope | Predeclared budget |
| --- | ---: | ---: |
| JSON read p95 | 19.8 ms | 1 second |
| Mutation p95 | 22.0 ms | 1.5 seconds |
| 1 MiB transfer p95 | 26.7 ms | 5 seconds |
| Sign-in p95 | 91.6 ms | 3 seconds |
| Post-burst recovery | 0.607 seconds | 30 seconds |

All applicable p95/p99 budgets passed; read p99 was bounded at two seconds and mutation p99 at three seconds. Limits were declared before the run and were not raised after observing results. The measured server envelope was one **4-CPU / 6-GiB inclusive parent cgroup**, containing the nested daemon, four apps, shared gateway and four backup workers. Steady50 used a mean **0.541 CPU cores** and peaked at **2.849 GB sampled parent memory**; the entire recorded run peaked at **2.910 GB sampled memory**. The separate workload client used **99.0 CPU seconds** and **451 MB peak RSS**. External ACME and off-host storage fixtures were outside the server quota. Child/container memory is diagnostic; it is not added again to the inclusive parent total.

These are controlled local-network/private-CA measurements. They are not DigitalOcean benchmarks, WAN latency, public Let's Encrypt issuance, an operator's provider test, or a guarantee for arbitrary file volume, polling, concurrency or hardware. The documented storage budget must also leave approximately **3× the selected snapshot's uncompressed size free for verification**, in addition to live data and images; see [backup workspace](BACKUP-AND-RECOVERY.md). The run does not establish performance with a full 500 GiB live dataset.

### Online capture and empty-host recovery

All four encrypted snapshots completed while Steady50 writers remained active. Those same snapshots were restored onto a **separate second Docker engine initially containing no app images, containers or data volumes**. Recovery built the selected current source, verified the archive/schema/configuration, cleared restored sessions, started HTTPS, passed all **64 API routes / 146 assertions** across the apps and produced a new verified backup.

| App | Capture during Steady50 | Empty-host restore including build, HTTPS, API proof and new backup |
| --- | ---: | ---: |
| Files | 10.05 s | 63.05 s |
| Projects | 8.21 s | 40.89 s |
| Notes | 7.40 s | 40.90 s |
| Chat | 7.50 s | 41.17 s |

The independent recovery audit matched **all 15,840 captured non-session SQL rows**, including private password hashes, against restored rows; all **300 referenced blobs**, including **34 Trash files**, matched exactly. Schema/integrity/foreign-key, runtime identity, original manifest provenance, autoincrement and semantic invariants passed. Recovery revoked **551 captured sessions** and had zero current sessions before later browser checks. New verifier fixtures were separately identified rather than counted as captured original rows. Sixteen recovered-app browser cases then passed. Hashes of the original source volumes remained byte-for-byte unchanged after recovery; the source engine remained stopped and intact.

These durations are observed fixture timings with the recorded caches/network, not recovery-time guarantees. Historical source/image IDs remain captured provenance; recovery verifies the exact supported app/schema/configuration and content rather than requiring an architecture-specific image ID to match.

### Release verification and evidence pointers

The final local suite passes **282 tests**. Final fixture cleanup also passed: all eight owned outer service containers and the owned network were removed, the recovered browser sessions were revoked, and no owned test containers remain active. Offline data/evidence volumes were retained; the shared Docker engine and unrelated resources were left intact. Hosted CI and anonymous source-download checks are separate release gates: the [v0.2.0 source release](https://github.com/Milad-Afdasta/OpenSW/releases/tag/v0.2.0) publishes a verification asset binding its exact Git commit, completed public CI run and source checksums. The [current workflow](https://github.com/Milad-Afdasta/OpenSW/actions/workflows/ci.yml) shows the exact ref checked. A prior release run or local build does not substitute for that evidence. Test infrastructure is removed only after the recovery/browser audits; private evidence and offline validation volumes are retained.

Private evidence is retained under the controlled run directory `/tmp/opensw-production-20261108/`; these are local audit references, not public downloads:

- `epoch-r11-2.json`: source/image/runtime identities.
- `workload-execution/full-analysis.json`, `full-report.json`, `cgroups-smoke-full.jsonl`: budgets, exact operations, raw samples and separate client CPU. Raw report SHA-256: `866b12adabd034f042f7a8af335edaa684b4a958dbc41fc8aa916e8a382e1d19`; measured runner SHA-256: `58ada7f34048312e5aec8ef1b4bd13c26c6222353b3a50ee71113edb6eebc94a`.
- `backups-during-load.json`, `restores.json`: exact capture IDs/windows and recovery outcomes.
- `qa-recovery/recovery-audit/{capture-summary,recovered-summary,complete-semantic-invariants,restored-api-proof}.json`, `qa-recovery/final-review.json`: independent captured/restored data proof and final R13 verdict.
- `original-preserved-after-restore.json`, `browser-recovery/browser.log`: original-volume preservation and the 16 recovered-app browser cases.

### Instruction improvement

Classification: **execution miss**. The existing standing contract already required canonical identity/confirmation, direct provider semantics and lifecycle/failure tests. Initial execution missed global Caddy route identity, interrupted-capture provenance, parent/task matching and durable/policy-aware status. Owner-level checks, exact historical fixtures, independent review, real fault injection and rebuilt browser/recovery tests now enforce those invariants. **No global instruction edit was warranted.** The app AGENTS guides are requested product setup documentation.

The historical v0.1.0 material below applies only to that earlier release and its recorded image epochs.

## Historical v0.1.0 validation

This record distinguishes completed development and review evidence from the final release checks. It does not turn a source review, an unrun case, or an earlier build into a pass for a later build. Date: 7 November 2026.

The historical governing model was [OPENSW-ISOLATED-R5](https://github.com/Milad-Afdasta/OpenSW/blob/v0.1.0/docs/MODEL-CLOSURE.md), revision 5 of that release's R0–R5 chain. Each app owns its process/container, database, users, sessions, storage, configuration, and origin. All three independent QA reviewers tried to falsify that exact model and returned **APPROVE OPENSW-ISOLATED-R5**. Their implementation findings violated existing invariants; those findings did not establish a model counterexample.

## Three completed contributor phases

| Phase | Three contributors and their independent responsibilities | Coverage and evidence |
| --- | --- | --- |
| 1. Plan and design | Product/workflows; architecture/persistence/deployment; interface/design | Each planned Files, Projects, Notes, and Chat. The three planners approved the exact R5 model before the affected implementation. Owners, contracts, retained states, failure/cleanup behavior, isolated deployment, and the minimum feature scope are recorded in the model. |
| 2. Develop | Backend/persistence contributor; frontend contributor; integration/test contributor, with root-owned installer/documentation integration | Each contributed across all four apps. Application implementation and the first pinned Docker validation epoch completed before the separate QA cohort began. Final corrections and their rebuilt release checks are tracked separately below. |
| 3. Independent manual QA and code review | Three different new agents: `opensw_qa_workflows`, `opensw_qa_privacy`, and `opensw_qa_persistence` | **Each manually exercised all four apps and reviewed code**, using separate browser contexts and fixture namespaces. Lenses were everyday workflows, privacy/identity/lifecycle, and persistence/API/installer security. All three reports are complete for the reviewed epoch. This is separate from the canonical browser harness and automated API verifier. |

Reviewers did not edit application code, restart services, or change the operator's password or active state. The root coordinated the services and consolidated the implementation corrections once.

## Reviewed build identity

The independent QA reports cover epoch `phase3-20261107-1`, ready at `2026-11-07T05:38:34.260556+00:00`, with source archive SHA-256 `012c5e2ebfeb6ea3baaf719539c6d4b52fdc9bb7269b5fe201bd6e2af4fa062f`.

| App | Reviewed immutable Docker image ID |
| --- | --- |
| Files | `sha256:db8e3aa0b38091e9a3986c8e5681eaa13d21173e653e63737babe37ee278b836` |
| Projects | `sha256:ac9727964fd54c120e7ca67217f1636a10f1d7814b154d68fcc97fcf3c5defae` |
| Notes | `sha256:bfa692c987398cdbe45ffba93a2eae261e2fe4c04bef603b5fd225bb0d2eef7c` |
| Chat | `sha256:fa18b740bc49579039085b0ec2a75e100bb44593f015f9cd03d6fba5eb4eb629` |

These identities are baseline evidence. The corrected source requires new image identities and affected-path checks; the final release ledger below must supply them.

## Completed functional evidence

Actual browser actions were coupled to real API or database assertions. Screenshots alone were not used to prove persistence, privacy, or successful writes.

| App | Independently exercised behavior | Observed canonical result |
| --- | --- | --- |
| Files | Folder creation; upload, rename, teammate download; duplicate rejection; child/parent Trash and restoration | Downloaded bytes match the upload. Metadata retains its parent/uploader. An active child blocks parent Trash; a trashed child does not. Restoring a child before its parent returns 409; restoring the parent never silently restores the child. |
| Projects | Project/task creation; description, assignee, status, priority, due date, comments; archive/restore; delayed task-detail selection | Saved fields and authored comments persist. Late detail for task A cannot populate or save task B. Archive/restore preserves content. The unsent-comment navigation exception discovered during review is listed below. |
| Notes | Markdown edit/preview, explicit Save, two writers, stale revision, dirty navigation, session expiry and reauthentication, delayed detail | A stale save returns 409 and preserves the draft. Keep editing and Discard perform the stated transition. Same-user reauthentication can retain the draft; another account cannot inherit it. Save identity/revision belong to the loaded selected page. |
| Chat | Channels and member-to-member DMs; send/receive, edits, tombstones, older history/search, unread positions, delayed responses and unknown acknowledgement | Nonparticipant/admin DM intrusion routes return 404 and do not expose the pair. Late A cannot fill B. Edits keep the message ID; deletion retains an empty tombstone. History beyond 100 is reachable. A lost send acknowledgement commits one message without automatic replay; its missing uncertainty notice was a review finding. |
| Shared account lifecycle | Create/deactivate/reactivate a temporary member; password change; logout; cross-app cookie checks | Disabled users are rejected on the next request while authorship remains. Password change revokes old sessions. A Files session does not authenticate Notes, even when copied into its cookie name. Operator credentials remain unchanged. |

All three reviewers exercised desktop `1440×900` and mobile `390×844`, in light and dark mode, across all four apps. Reports retain screenshots and assertions for text containment, alignment, contrast, missing controls, overlap, and document-width overflow. Review found a long Chat sidebar overlap and the root's canonical matrix found offscreen mobile navigation still accessible; neither is hidden by the successful captures. Some early theme screenshots caught the 150ms transition. Final root inspection covered settled default/dark sidebar styles, mobile menu containment and task-dialog controls; the capture harness additionally waits for task dialog removal after Close.

Direct read-only SQLite inspection passed for all four running containers: SQLite 3.46.1; schema version 1; DELETE journal; integrity `ok`; no foreign-key violations; correct app/instance ownership metadata; live `sqlite_master` DDL equal to the corresponding checked-in `apps/<app>/schema.sql`. Automated invariants also cover per-connection foreign keys, every canonical Projects catalog value, summary/detail contracts, pagination beyond 500 resources, archived/trashed rows, disabled authors/assignees, revision conflicts, and message tombstones. This is an initial release: no historical production row population or previously released client binary is claimed as tested.

## Consolidated findings and correction status

All corrections below were applied at their existing owners and checked against the rebuilt release epoch. The browser harness also found a selected-metadata pagination issue after 100 channels; the same collection-owner correction covers Projects. No API or database contract changed.

| Finding | Existing invariant / root correction | Evidence and current verdict |
| --- | --- | --- |
| P1: actual config/key/password inputs could enter a build archive when their filenames lacked a guessed secret extension | Build-context ownership now receives the actual private input paths and excludes resolved path and device/inode identity, including hardlink aliases | QA3 reproduced using fake files in an isolated tree, then independently verified all four arbitrary-name/path-alias exclusions. Focused regression passed. No real credential was used in that reproduction. |
| P2: closing a task discarded an unsent comment without a guard | One task draft navigation owner includes field and comment dirtiness, while field Save remains separate | QA1 real Close/reopen evidence; corrected; final real-browser regression passes |
| P2: non-ASCII CSRF header produced 500 | Malformed token rejection belongs to shared write authorization; reject without mutation | QA2 real HTTP evidence; corrected; malformed-token invariant tests and real-browser 403/session-preservation check pass |
| P2: Chat send uncertainty disappeared after switching conversations | Account/conversation identity owns the draft and its delivery uncertainty; no automatic send replay | QA2 held a real committed acknowledgement, changed conversations, and verified one canonical message; corrected; final real-browser regression passes |
| P2: a long Chat channel list overlapped the DM heading | Sidebar/list sizing must preserve its existing scrolling/layout ownership | QA2 settled desktop light/dark screenshots; corrected; 15-channel sidebar bounds and settled light/dark visual checks pass |
| Root visual gate: offscreen closed mobile navigation remained accessible | Closed navigation must be hidden and noninteractive; open/close focus belongs to the shell | Corrected; all four apps pass closed-navigation accessibility, focus, and interaction checks across desktop/mobile light/dark |

Initial harness mistakes remain in local reports for audit: acting before a matching folder heading, ambiguous or incorrect button labels, and Node transport not resolving browser-only `.localhost` aliases. Corrected focused runs waited for confirmed identity and used the actual controls. These mistakes are not counted as product defects or silently overwritten with passing results.

## Automated and installation evidence

The root's final corrected-source combined run reports **78 passing tests: 47 backend/API, 17 installer, and 14 live-verifier tests**. It reports one test-only Starlette/httpx deprecation warning. Strict TypeScript typecheck and selected-app production builds also pass for all four apps after the consolidated source corrections. These local results are distinct from the final rebuilt runtime and hosted CI gates below. The shipped verifier's route manifest is mechanically compared to each selected app's OpenAPI surface; its integration tests use real HTTP servers and prove that the existing operator's content and credentials are preserved.

The root reports the earlier Docker live verifiers passing all 62 selected-app route combinations across the four apps. A retained SSH Files fixture log independently records 16/16 required routes passing, 40 PASS, 0 FAIL, 0 BLOCKED, together with an installer rerun retaining the immutable image. This validates the existing-server SSH transport against a controlled fixture; it is not an actual paid-cloud deployment or public TLS test.

Reproduction entry points are documented in [the API guide](API.md), [the browser harness guide](../tests/browser/README.md), and each app's `AGENTS.md`. CI is defined in [the workflow](../.github/workflows/ci.yml): combined API/invariant tests, four selected frontend typecheck/build jobs, and four Docker source builds. A committed workflow is not evidence that GitHub has executed it successfully.

## Final release ledger

The tested source-build input archive is: `3f7535e7b91adb8bc214b728730c21b260ec6907a26b9c54003ae3310c32dddf`, epoch `release-20261107-3`. All four containers confirmed their selected app, initialized account, and schema version 1. Later app-guide whitespace cleanup and root documentation/test edits do not change executable app code; the tested images are development evidence, not distributed binaries. Public CI rebuilds the published checkout.

| Gate | Status | Evidence |
| --- | --- | --- |
| Combined invariant/integration tests | **PASS** | `PYTHONPATH=backend .venv/bin/python -m pytest -q backend/tests tests`: 78 passed (47 backend/API + 17 installer + 14 live verifier); one test-only Starlette/httpx deprecation warning. `pytest-release.log` |
| Frontend typecheck/build | **PASS** | Strict typecheck and production builds for all four variants, including final source-image builds. `<app>-release-build.log` |
| Dependency advisories and consistency | **PASS at check time** | `npm audit --json`: 0 reported advisories; `uv pip check --python .venv/bin/python`: all 23 installed packages compatible; pip-audit on all 15 locked runtime Python distributions: 0 known advisories. Advisory databases are incomplete and time-dependent; this is not a security certification. `npm-audit.json`, `python-audit.json` |
| Canonical real-browser workflow matrix | **PASS** | 16/16: each app × desktop/mobile × light/dark, with persisted-state assertions and populated screenshots. `browser-release.log`, `browser-release/` |
| Complete final browser run | **PASS — 52/52 in one run** | Fresh independent installations: 16 everyday workflows plus 36 regressions, across desktop/mobile light/dark, no retries. Includes all eight >100-row creation/refresh/deep-link cases, all four lost-acknowledgement cases, comment guards, native scrolling and sidebar accessibility. `browser-fresh-final.log`, `browser-fresh-final/`; an additional four Projects cases pass with dialog-removal waits before final screenshots (`projects-settled-final.log`). |
| Real native-focus lifecycle | **PASS** | Headed Chromium, native focus behavior restored; actual second-tab focus suppresses Chat polling/read receipts; return to Chat resumes and confirms the message. Non-ASCII CSRF receives 403 and preserves the session. No document property mocks. `focus-release.log` |
| Installed full API verifiers | **PASS** | Files 16 routes/40 assertions; Projects 17/35; Notes 12/30; Chat 17/38. Total 62 route combinations, 143 assertions, 0 FAIL, 0 BLOCKED. `<app>-release-api.log` and repeated on fresh installations in `<app>-fresh-final-api.log` |
| Recreated-container persistence | **PASS** | Four matching original records reopen after rebuilding with retained volumes; exact Files bytes, task details, note body/revision, edited Chat message; other-app endpoints 404. `qa-persistence/restart-report.json`, `*-after-restart.png` |
| Existing-server SSH transport | **PASS on isolated fixture** | Source install, readiness, retained-image rerun and complete Files verifier over real SSH to a controlled Docker-host fixture. `ssh-install-and-api.log` |
| Backup and recovery | **PASS on isolated fixture** | Stopped Files volume archived/restored to a new empty volume; database/state/all blobs SHA-256 equal; separate restored container boots and passes the complete Files API verifier; original restarted, temporary recovery resources removed. `backup-recovery-report.json`, `restored-files-api.log` |
| Test-service cleanup | **PASS** | All eight owned validation/fresh containers stopped, all their sessions revoked, retained data integrity/FKs checked, SSH fixture containers removed; shared Docker engine left running. Private evidence and validation volumes retained outside Git. `cleanup-report.json` |
| Sensitive input exclusion/publication audit | **PASS** | Actual config/key/password paths and inode aliases excluded from build context; staged source scanned against actual private QA credentials, keys and runtime-data file patterns. Private evidence remains outside Git. |
| Public source and hosted CI | **PASS** | Anonymous repository/README/source-ZIP access confirmed; extracted ZIP plans all four installs successfully. Public [CI run 42345577539](https://github.com/Milad-Afdasta/OpenSW/actions/runs/42345577539) passed all nine jobs on commit `d3c3d0dc56402cb53c8e28a138246381b25ee4bf`. Current-commit status is visible in [Actions](https://github.com/Milad-Afdasta/OpenSW/actions/workflows/ci.yml). The release is source-only. |

| App | Final tested immutable image ID |
| --- | --- |
| Files | `sha256:009b55211c88c3cc4d573b32bc231e8dcf8965abbb514424aa79b1e69fc1e7f1` |
| Projects | `sha256:db44bb0097bc8d344dc821331a26a9782afad1faa847bbae2a05122d960c2b6d` |
| Notes | `sha256:bb208e077895b76ec9d349a52b7285782998b1f58c7d9eceb6a2b91c9a55c867` |
| Chat | `sha256:abe2d394024fa4f195904f4ae7cd0b13269365370a1e7d2bea0b66ac0ec3c331` |

The complete final 52-case matrix used four additional first-boot installations, with source archive `2c928df7f05b2d50f6c9b998ed67ecfa747971bbac3971532d9d0f7c4a18af9b`. For every app, a deterministic hash of all installed backend, schema/migration, shared catalog, verifier and frontend distribution bytes exactly matched its earlier retained-data validation container. This directly connects fresh-install browser evidence to the retained-data/focus/API evidence despite source-archive differences limited to non-executable app guides. `fresh-epoch.json` records the full identities. All four complete API verifiers also passed on these populated fresh installations after the browser run.

| App | Fresh final image ID |
| --- | --- |
| Files | `sha256:58eff364a3e4c26e16ab9561179acf28764c92f4585910d7f21e5d1374895d76` |
| Projects | `sha256:d23df2f7eee123f874265564957c9cbda8072d5db230933400dacaef2f328105` |
| Notes | `sha256:8f60b7a8a97451e52e43a76f788d5990ee9e516e9045903074c2054a82c58938` |
| Chat | `sha256:ad97595d51f3d6093780054f3115ca6e932de35a1920b5e0764f108bd2c49766` |

Earlier expanded runs exposed harness setup races (repeated sign-in bursts, checking navigation before confirmed route changes, and manual pagination competing with automatic selected-resource loading). Those failures remain recorded privately; they are not counted as passing runs. The final run used fresh fixtures, real server-issued in-memory cookies, confirmed navigation, native scrolling, and no forced application state. A separate deliberately rejected sign-in returned the expected failed scenario while clearing its password from every failure artifact.

## Audit evidence retained locally

These are local audit pointers from the development run, not public downloads. Private credential files and credential-bearing diagnostics must not be published with them. Paths are under `/Users/null/Documents/Codex/2026-11-07/OpenSW/`:

| Evidence | Path relative to that local directory |
| --- | --- |
| QA1 workflow report and its exact run/harness limitations | `qa-workflows/FINAL-REPORT.md` |
| QA2 privacy/lifecycle report, intrusion assertions and explicit NOT_RUN cases | `qa-privacy/REPORT.md` |
| QA3 persistence/installer report and consolidated results | `qa-persistence/REPORT.md`, `qa-persistence/consolidated-report.json` |
| Actual live database/master-schema proof | `qa-persistence/live-database-proof.json` |
| Fake-source credential inclusion before correction and exclusions after correction | `qa-persistence/source-context-exclusion.json`, `qa-persistence/source-context-fix-proof.json` |
| Root-controlled retained-state check prepared by QA3 | `qa-persistence/restart-check.cjs`, `qa-persistence/restart-report.json` — all four PASS |
| Controlled SSH install/rerun and Files live verifier | `ssh-install-and-api.log` |
| Root final browser/API/restart/CI evidence | `browser-release.log`, `browser-release/`, `browser-fresh-final/`, `browser-fresh-final.log`, `<app>-fresh-final-api.log`, `<app>-release-api.log`, `focus-release.log`; public workflow linked above |

## Limits and unrun cases

- The independent QA2 run could not establish a native hidden/unfocused document and recorded that case NOT_RUN. Root subsequently proved the real **unfocused** lifecycle with headed Chromium, native focus restored, and an actual second tab. A separate genuinely hidden-document transition remains NOT_RUN; no result is inferred from a still-visible document.
- Reviewers used ready, shared, disposable validation services. Clean first boot, service restart, and release publication are root gates; the independent reports do not claim to have performed them.
- The remote proof uses an existing-server SSH fixture. New cloud-server provisioning, real provider credentials, internet DNS and public HTTPS automation are outside this release's verified scope. Public exposure requires an appropriately configured HTTPS deployment.
- Source builds use pinned base-image digests, locked packages, and recorded source/archive/image identities. There is no independently demonstrated bit-for-bit reproducible build and no verified prebuilt-package distribution claim; see [security and builds](SECURITY-AND-BUILDS.md).
- “More than 95% of the features you need” is an **unmeasured product goal**, not a measured coverage result. The implemented workflow checks do not establish a universal feature percentage. Use [feature fit](FEATURE-FIT.md) against your own requirements. Hosting costs, workload capacity, uptime, recovery time and maintenance effort are not guaranteed by these checks.
- QA cleanup is soft: files go to Trash, notes/projects/tasks are archived, messages become tombstones, and test members are disabled. Some fixture rows, channels and DMs intentionally remain. No permanent-delete or automatic destructive cleanup capability is assumed.

## Instruction improvement

Classification: **execution miss**. The existing standing execution contract already required these checks: “Ownership and scope” requires tracing data through every relevant flow and distinguishing requested from confirmed state; “Testing and validation” requires lifecycle/failure coverage and default/dark visual inspection; “Evidence and execution behavior” forbids logging secrets. Execution initially omitted actual sensitive input identities, comment drafts in navigation ownership, selected metadata beyond page one, delivery uncertainty under conversation changes, malformed-token rejection, and settled/hidden-interaction layout checks. Owner corrections, invariant regressions, rebuilt real-browser/API checks, and a real rejected-sign-in artifact-redaction check enforce the product fix. **No global instruction edit was warranted.**

The per-app `AGENTS.md` files are separately and explicitly requested product documentation for AI-assisted setup and verification. They are not incident-specific additions to global agent instructions.
