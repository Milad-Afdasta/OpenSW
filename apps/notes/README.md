# OpenSW Notes

Keep your team's useful knowledge easy to write and find.

This is an independent, self-hosted application. It has its own login accounts, server process, database, storage, and configuration. You can run it alone; none of the other OpenSW apps is required.

## Everyday features

- Searchable pages
- Plain Markdown editing with a safe preview
- Explicit Save and a visible saved state
- Revision checks that reject conflicting edits
- Archive, restore, and member-only page links

## Screenshots

Desktop light mode with demo data. Select an image to open it at full size.

### Page editor

Write and save a shared page in plain Markdown.

[![OpenSW Notes — page editor](../../docs/screenshots/notes/01-editor.png)](../../docs/screenshots/notes/01-editor.png)

### Markdown preview

Read the saved page with formatted headings, lists and emphasis.

[![OpenSW Notes — markdown preview](../../docs/screenshots/notes/02-preview.png)](../../docs/screenshots/notes/02-preview.png)

### Archived page

Keep older pages available as a read-only reference, with a Restore action.

[![OpenSW Notes — archived page](../../docs/screenshots/notes/03-archived-page.png)](../../docs/screenshots/notes/03-archived-page.png)

## Get started

From the repository root, run:

```sh
./install.sh
```

Choose **OpenSW Notes**, then **this computer** or **an existing server**. The installer asks for the necessary details, checks prerequisites, builds the app from the public source, and verifies readiness.

For a supplied configuration file:

```sh
./install.sh --config apps/notes/config.example.toml
```

Copy the example to a private `*.local.toml` file before entering server details. The normal fields are the team name, where to run, and—only for a server—its address, login name, and SSH key file. The separate `[advanced]` section is optional. Passwords are entered through a hidden prompt, not stored in a public example.

Docker must be installed and running. The small installer also needs Python 3.11 or newer. The app's Node and Python dependencies are built inside Docker; you do not install those development packages on the host.

The default local port is **8083**. The installer prints the actual address after the readiness check. Private existing-server access uses a printed SSH tunnel command. For public access, enable the optional `[https]` and `[backups]` sections: the installer manages certificates and encrypted backups using your public IPv4 address (one app/server) or DNS hostname, server and authorized storage. It does not purchase domains, servers or storage. See the [configuration guide](../../docs/CONFIGURATION.md).

## First useful workflow

Create a page, write a short note, preview it, and save. Open it as another teammate to verify the saved content.

## Accounts and data

Every active teammate in this app can access its ordinary team content. Accounts in other OpenSW apps are separate, even when their usernames match.

Pages, revisions, authors, and archived records live in this app's own SQLite database. Conflicting writes are rejected; copy an unsaved draft before deliberately loading another editor's newer version.

Stopping or rerunning the same installation preserves its volume and accounts. Optional encrypted off-host backups run every six hours by default, with a first full restore check and weekly restore verification. Check `./install.sh status --config path/to/notes.local.toml`; request a manual capture with `./install.sh backup --config path/to/notes.local.toml`. Keep the recovery password safely outside the server. See [backup and recovery](../../docs/BACKUP-AND-RECOVERY.md) for retention, health and recovery onto an empty target.

## Deliberately outside this release

- Databases and formulas
- A rich block editor
- Nested page trees
- Real-time co-editing
- Public publishing and third-party embeds

OpenSW focuses on a defined everyday feature set. It does not claim feature parity with a commercial platform or a measured percentage of every team's requirements.

## Source and verification

Run the complete installed-app check with `./verify.sh --config path/to/notes.local.toml`. It creates temporary test members and reports every required API route as passed, failed, or blocked. See the [API examples and diagnostics](../../docs/API.md), including cookie/CSRF requests with curl, and this app's [AI setup instructions](AGENTS.md).

The [main README](../../README.md) explains installation and ownership. The [approved model](../../docs/MODEL-CLOSURE.md) documents the data and lifecycle invariants. API and browser checks are maintained under [tests](../../tests); the public CI workflow builds all four app variants.
