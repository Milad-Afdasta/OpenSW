# OpenSW API and end-to-end verification

Each app exposes only its own API and its own accounts. All resource IDs belong to that app's database. An ID or session from another app is not interchangeable.

The easiest complete check is:

```sh
./verify.sh --config path/to/app.local.toml
```

The wrapper selects the exact installed app and prompts privately for credentials. Private HTTP verification runs `tools/verify_api.py` inside the container. Public verification runs the same canonical verifier against the actual configured HTTPS origin with normal certificate/hostname checks and Secure cookies; an explicit private CA may be supplied in `[https]`. The computer running verification must resolve and reach that hostname. The verifier checks every declared business route, reports named results and diagnostics, and exits nonzero for any failed or unrun required check. Route manifests are mechanically compared with the actual FastAPI API schema in the test suite.

The tool creates two temporary members and namespaced test content. It never resets an existing administrator's password or disables an existing account. Cleanup archives, trashes, or tombstones only confirmed test records, then deactivates its two temporary members. Those rows and any test Chat channel/DM remain in storage; no permanent deletion or automatic retention limit is imposed.

For a direct network connection:

```sh
python3 tools/verify_api.py --app notes --url http://notes-team.localhost:8083 --username admin
```

Use the address printed by the installer. `--password-file /private/path/password` supports a private local password file. If an intentionally different connection address is used, `--origin` must remain the configured application origin. Inside-container verification connects to the container's own loopback service and separately preserves that configured origin.

For public deployments use the configured HTTPS URL; `--ca-file /private/trusted-ca.pem` is available for an explicitly trusted private CA. Do not substitute HTTP, disable certificate checks or downgrade Secure cookies to make a verifier pass. `./verify.sh --config path/to/app.local.toml --username admin --password-file /private/admin-password.txt` uses that installation's configured transport/trust settings.

## Authenticated requests with curl

Browser writes require the exact configured `Origin` and the session's `X-CSRF-Token`. The opaque session cookie is HttpOnly; API clients keep it in a private cookie jar. Do not paste passwords, cookies, or session JSON into an issue or public log.

The following local example stores credentials/session data only in a newly created private temporary directory. Set the URL to the actual installed address:

```sh
umask 077
opensw_api_tmp=$(mktemp -d)
opensw_url=http://notes-team.localhost:8083

python3 -c 'import getpass,json,sys; sys.stderr.write("Username: "); username=sys.stdin.readline().strip(); print(json.dumps({"username":username,"password":getpass.getpass("Password: ")}))' > "$opensw_api_tmp/login.json"

curl --fail-with-body --silent --show-error \
  --header "Origin: $opensw_url" --header 'Content-Type: application/json' \
  --cookie-jar "$opensw_api_tmp/cookies" \
  --data-binary "@$opensw_api_tmp/login.json" \
  "$opensw_url/api/auth/login" > "$opensw_api_tmp/session.json"

# Keep the session's CSRF token in a private header file, outside process arguments.
python3 - "$opensw_api_tmp/session.json" "$opensw_api_tmp/headers" "$opensw_url" <<'PY'
import json
import os
import sys

with open(sys.argv[1]) as source:
    csrf_token = json.load(source)["csrf_token"]
fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as headers:
    headers.write(f"Origin: {sys.argv[3]}\n")
    headers.write(f"X-CSRF-Token: {csrf_token}\n")
    headers.write("Content-Type: application/json\n")
PY

curl --fail-with-body --silent --show-error \
  --cookie "$opensw_api_tmp/cookies" \
  "$opensw_url/api/notes"

# This explicitly creates a new page in your own Notes workspace.
curl --fail-with-body --silent --show-error \
  --cookie "$opensw_api_tmp/cookies" \
  --header "@$opensw_api_tmp/headers" \
  --data '{"title":"My API check","body":"Created through the public API."}' \
  "$opensw_url/api/notes"

# Remove only the temporary credential files this example created.
rm -f "$opensw_api_tmp/login.json" "$opensw_api_tmp/session.json" "$opensw_api_tmp/cookies" "$opensw_api_tmp/headers"
rmdir "$opensw_api_tmp"
```

To archive that page later, use its confirmed ID and current revision with `PATCH /api/notes/{id}` and `{"archived":true,"expected_revision":CURRENT_REVISION}`. A stale revision returns `409`; never silently overwrite it.

## Shared routes

These routes operate separately inside each app:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Confirm selected app and ready schema |
| GET | `/api/me` | Current user, CSRF token, app identity, team name, catalog |
| POST | `/api/auth/login` | Create an opaque cookie session |
| POST | `/api/auth/logout` | Revoke this session |
| POST | `/api/auth/password` | Check old password, change password, revoke all caller sessions |
| GET | `/api/users` | Paginated teammate identities, including retained inactive authors |
| POST | `/api/users` | Administrator creates a member |
| PATCH | `/api/users/{id}` | Administrator activates/deactivates a member |

## App routes

| App | Methods and paths |
|---|---|
| Files | `GET /api/files`; `POST /api/files/folders`; `POST /api/files/upload`; `GET/PATCH/DELETE /api/files/{id}`; `POST /api/files/{id}/restore`; `GET /api/files/{id}/download` |
| Projects | `GET/POST /api/projects`; `GET/PATCH /api/projects/{id}`; `GET/POST /api/projects/{id}/tasks`; `GET/PATCH /api/tasks/{id}`; `GET/POST /api/tasks/{id}/comments` |
| Notes | `GET/POST /api/notes`; `GET/PATCH /api/notes/{id}` |
| Chat | `GET/POST /api/chat/channels`; `GET/POST /api/chat/dms`; `GET /api/chat/conversations/{id}`; `GET/POST /api/chat/conversations/{id}/messages`; `PATCH/DELETE /api/chat/messages/{id}`; `POST /api/chat/conversations/{id}/read` |

List pages return `{items,next_cursor}` with at most 100 records. Follow `next_cursor`; do not interpret one page as the entire collection. Files adds `breadcrumbs` and `usage_bytes`. Notes and task lists are summaries: fetch the detail endpoint before editing. Chat returns the latest or requested older snapshot as `{items,has_more}`, using `before_id` for older history.

Notes/tasks writes carry `expected_revision`. Calendar due dates are `YYYY-MM-DD` strings. Files upload uses multipart form data with `file` and optional `parent_id`; the server enforces actual received bytes. File downloads are attachments, with `nosniff`, rather than active same-origin previews.

In Chat, administrator status does not grant access to another pair's DM. Membership checks apply to history, search, read markers, edits, and conversation metadata. Only the author may edit/delete their messages; deletion keeps an empty tombstone.

## Diagnosing a failed check

| Result | What to inspect |
|---|---|
| Connection failure / unconfirmed write | Exact target process and logs; read current state before any new write |
| `401` | Selected app, supplied credentials, expiry, active account |
| `403` | Origin, CSRF token, role, conversation membership |
| `404` | App/resource identity; other apps' APIs are intentionally absent |
| `409` | Current revision, active parent/project, duplicate name, or retained state |
| `413` | Configured file size limit |
| `422` | Named request field and the current contract |
| `429` | Explicit login admission rejection; respect `Retry-After` and inspect staged sign-in/abuse pressure |
| `5xx` | Storage health and the exact app container's logs |

The verifier does not automatically retry side-effecting requests after a timeout or lost response. `BLOCKED` is not a pass. A received HTTP response and a passed route are reported separately, so unreachable or unrun APIs cannot inflate coverage.

## Development checks

These optional checks run from the repository root and require a local Python development environment. On a fresh checkout, create it and install the committed, hash-locked test dependencies first:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r backend/requirements-dev.txt
```

Reuse that environment for subsequent test runs. Reinstall dependencies when the committed requirements change; do not recreate an existing environment unnecessarily. This setup is separate from normal Docker installation and the installed-app `./verify.sh` check, which do not require these host test packages.

```sh
PYTHONPATH=backend .venv/bin/python -m pytest -q tests backend/tests
```

The suite uses actual SQLite databases, real cookie/CSRF sessions, all four selected routers, complete producer JSON, catalog values, pagination beyond 500 resources, schema equivalence, retained identities, and real HTTP runs of the shipped verifier. The optional [browser harness](../tests/browser/README.md) performs real interface workflows in light/dark desktop/mobile views.
