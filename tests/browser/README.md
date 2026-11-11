# Optional real-browser checks

This development harness uses Chromium to perform actual user actions against running OpenSW apps. It checks server-confirmed persistence and captures populated desktop/mobile views in light and dark mode. It does not replace independent manual QA, security review, or the complete post-install API verifier.

Use disposable test deployments and accounts. The harness creates uniquely named test records. Files, projects, and notes are softly cleaned up; Chat channels and message tombstones remain because the first release has no permanent-delete API.

From the repository root:

```sh
cd web
npm ci
cd ..
web/node_modules/.bin/playwright install chromium
```

Create a **private** JSON file outside Git, set its permissions to `0600`, and fill in the actual installed URLs and test administrator credentials:

```json
{
  "files": {"url": "http://files-team.localhost:8081", "username": "admin", "password": "PRIVATE_TEST_PASSWORD"},
  "projects": {"url": "http://projects-team.localhost:8082", "username": "admin", "password": "PRIVATE_TEST_PASSWORD"},
  "notes": {"url": "http://notes-team.localhost:8083", "username": "admin", "password": "PRIVATE_TEST_PASSWORD"},
  "chat": {"url": "http://chat-team.localhost:8084", "username": "admin", "password": "PRIVATE_TEST_PASSWORD"}
}
```

Run all four apps across four viewport/theme combinations:

```sh
OPENSW_QA_CONFIG=/private/path/browser-accounts.json \
  web/node_modules/.bin/playwright test --config tests/browser/playwright.config.cjs
```

For an intentionally selected single app, provide that app's entry and set `OPENSW_QA_APP=files` (or `projects`, `notes`, `chat`). Omitted apps are not described as tested.

Screenshots and failure artifacts go to ignored `artifacts/browser/`, or the path supplied in `OPENSW_BROWSER_ARTIFACTS`. Credential-bearing Playwright traces are disabled. Do not publish the credential JSON, test passwords, cookies, or private workspace screenshots.

Treat the entire artifact directory as private: Playwright failure context can include DOM input values and private content. The harness clears visible password fields on rejected sign-in before failure capture; review/redact every artifact before sharing it. A burst of checks against one account can trigger the real 15-attempt, one-minute sign-in limit; wait for that window rather than weakening authentication.

Every test must finish successfully for the selected coverage to pass. Retries are disabled so an unknown mutation outcome does not silently become a repeated user action. Inspect the actual failure and confirmed server state before running the scenario again.

The regression cases also exercise unsent task comments, committed Chat sends with lost acknowledgements, sidebar accessibility/overlap, and selected resources beyond the first 100 rows. They deliberately create more fixtures; use a disposable installation. `OPENSW_QA_APP` limits them to the selected app too.

For a separate native focus lifecycle check, run this with a desktop display available:

```sh
OPENSW_QA_CONFIG=/private/path/browser-accounts.json node tests/browser/focus-lifecycle.cjs
```

It opens headed Chromium, restores native focus behavior normally overridden by Playwright, switches to another real tab, confirms Chat stops polling/read receipts while unfocused, then confirms recovery on focus. It also verifies malformed CSRF returns 403 without logging out. It never overwrites browser document visibility or focus properties. Failure to establish the native focus precondition is a failed/unrun environment check, not a product pass.
