# OpenSW Chat

Channels and private conversations, with less clutter.

This is an independent, self-hosted application. It has its own login accounts, server process, database, storage, and configuration. You can run it alone; none of the other OpenSW apps is required.

## Everyday features

- Team channels
- Private one-to-one direct messages
- Text messages and older history
- Search within a conversation
- Edit or delete your own messages
- Unread counts and visibility-aware refresh

## Screenshots

Desktop light mode with demo data. Select an image to open it at full size.

### Team channel

Follow a shared conversation with messages from several teammates.

[![OpenSW Chat — team channel](../../docs/screenshots/chat/01-channel.png)](../../docs/screenshots/chat/01-channel.png)

### Direct message

Talk privately with one teammate.

[![OpenSW Chat — direct message](../../docs/screenshots/chat/02-direct-message.png)](../../docs/screenshots/chat/02-direct-message.png)

### Conversation search

Find matching messages within the selected conversation.

[![OpenSW Chat — conversation search](../../docs/screenshots/chat/03-search.png)](../../docs/screenshots/chat/03-search.png)

## Get started

From the repository root, run:

```sh
./install.sh
```

Choose **OpenSW Chat**, then **this computer** or **an existing server**. The installer asks for the necessary details, checks prerequisites, builds the app from the public source, and verifies readiness.

For a supplied configuration file:

```sh
./install.sh --config apps/chat/config.example.toml
```

Copy the example to a private `*.local.toml` file before entering server details. The normal fields are the team name, where to run, and—only for a server—its address, login name, and SSH key file. The separate `[advanced]` section is optional. Passwords are entered through a hidden prompt, not stored in a public example.

Docker must be installed and running. The small installer also needs Python 3.11 or newer. The app's Node and Python dependencies are built inside Docker; you do not install those development packages on the host.

The default local port is **8084**. The installer prints the actual address after the readiness check. Private existing-server access uses a printed SSH tunnel command. For public access, enable the optional `[https]` and `[backups]` sections: the installer manages certificates and encrypted backups using your public IPv4 address (one app/server) or DNS hostname, server and authorized storage. It does not purchase domains, servers or storage. See the [configuration guide](../../docs/CONFIGURATION.md).

## First useful workflow

Create two teammates, send a channel message, and exchange a direct message. A third teammate—including an administrator—must not be able to read that DM.

## Accounts and data

Every active teammate in this app can access its ordinary team content. Direct messages are the exception: only the two participants can read them. Administrator status does not grant access to another pair's DM. Accounts in other OpenSW apps are separate, even when their usernames match.

Messages, channel membership, private conversation pairs, and read positions live in this app's own SQLite database. Deleted messages retain an empty tombstone so history and cursors remain stable.

Stopping or rerunning the same installation preserves its volume and accounts. Optional encrypted off-host backups run every six hours by default, with a first full restore check and weekly restore verification. Check `./install.sh status --config path/to/chat.local.toml`; request a manual capture with `./install.sh backup --config path/to/chat.local.toml`. Keep the recovery password safely outside the server. See [backup and recovery](../../docs/BACKUP-AND-RECOVERY.md) for retention, health and recovery onto an empty target.

## Deliberately outside this release

- Calls and screen sharing
- Threads and reactions
- Attachments
- Bots and workflow integrations
- Private channels, group DMs, and federation

OpenSW focuses on a defined everyday feature set. It does not claim feature parity with a commercial platform or a measured percentage of every team's requirements.

## Source and verification

Run the complete installed-app check with `./verify.sh --config path/to/chat.local.toml`. It creates temporary test members and reports every required API route as passed, failed, or blocked. See the [API examples and diagnostics](../../docs/API.md), including cookie/CSRF requests with curl, and this app's [AI setup instructions](AGENTS.md).

The [main README](../../README.md) explains installation and ownership. The [approved model](../../docs/MODEL-CLOSURE.md) documents the data and lifecycle invariants. API and browser checks are maintained under [tests](../../tests); the public CI workflow builds all four app variants.
