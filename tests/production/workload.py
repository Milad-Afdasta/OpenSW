#!/usr/bin/env python3
"""Real HTTPS workloads for all four independent OpenSW apps.

Use a mode-0600 JSON file with exactly four app keys, each containing
{url, username, password}. The operator is used only to create this run's
confirmed namespaced members. No command is replayed after an uncertain result.
The smoke profile is a 30-second diagnostic, never capacity evidence.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import resource
import secrets
import ssl
import stat
import sys
import time
from urllib.parse import urlsplit

import httpx


APPS = ("files", "projects", "notes", "chat")
ROOT = Path(__file__).resolve().parents[2]
CATALOG = json.loads((ROOT / "shared/catalog.json").read_text())
MIB = 1024 * 1024
AUTH_INTERVAL = 1.4
DOCUMENT_BYTES = {"notes": 4096, "projects": 1024}
BUDGETS = {"read": {"p95": 1.0, "p99": 2.0}, "write": {"p95": 1.5, "p99": 3.0}, "auth": {"p95": 3.0}, "transfer": {"p95": 5.0}}


@dataclass(frozen=True)
class Stage:
    name: str
    users: int
    seconds: int


STAGES = {
    "full": (Stage("ramp10", 10, 30), Stage("ramp25", 25, 30), Stage("steady50", 50, 300), Stage("burst100", 100, 30), Stage("recovery10", 10, 45)),
    "smoke": (Stage("diagnostic10", 10, 30),),
}
SEEDS = {"full": {"tasks": 500, "notes": 500, "files": 200, "messages": 10000}, "smoke": {"tasks": 50, "notes": 50, "files": 15, "messages": 100}}


class StopRun(RuntimeError):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def percentile(values, fraction):
    """Nearest-rank percentile: small samples do not understate the tail."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def document_text(app, marker):
    """Readable deterministic UTF-8 content with an exact document byte size."""
    target = DOCUMENT_BYTES[app]
    heading = f"# {marker}\n\n"
    remaining = target - len(heading.encode())
    if remaining < 0:
        raise ValueError("The unique document marker exceeds the fixture size.")
    paragraph = "The team records the plan, current decisions, owner, next action, and evidence for this work.\n"
    return heading + (paragraph * math.ceil(remaining / len(paragraph)))[:remaining]


def classify(method, status, expected):
    if status is None or status >= 500:
        return "unconfirmed_write" if method not in {"GET", "HEAD"} else "unexpected"
    if status in expected:
        return "confirmed" if 200 <= status < 300 else "expected_rejection"
    return "unexpected"


def private_config(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
        raise ValueError("The workload config must be an owned regular mode-0600 file.")
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or set(value) != set(APPS):
        raise ValueError("The config must contain exactly files, projects, notes and chat.")
    for app, settings in value.items():
        if not isinstance(settings, dict) or set(settings) != {"url", "username", "password"} or not all(isinstance(item, str) and item for item in settings.values()):
            raise ValueError(f"Invalid {app} endpoint credentials.")
        parsed = urlsplit(settings["url"])
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Each target must be one real HTTPS origin without credentials or a path.")
        parsed.port
        settings["url"] = settings["url"].rstrip("/")
    if len({settings["url"] for settings in value.values()}) != len(APPS):
        raise ValueError("Each independent app must have its own HTTPS origin.")
    return value


class Fixtures:
    """Only confirmed, run-owned IDs authorize mutations; never operator IDs."""

    def __init__(self, prefix):
        self.prefix = prefix
        self.rows = defaultdict(dict)

    def remember(self, kind, row):
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0:
            raise StopRun("A mutation did not return a complete confirmed resource identity.")
        if kind == "users" and (not isinstance(row.get("username"), str) or not row["username"].startswith(self.prefix + "-") or row.get("role") != "member"):
            raise StopRun("An operator or foreign user cannot become a workload-owned member.")
        self.rows[kind][row["id"]] = row

    def owns(self, kind, identity):
        return identity in self.rows[kind]

    def admit(self, method, path, payload=None, filename=None):
        if method in {"GET", "HEAD"} or path == "/api/auth/login":
            return
        payload = payload or {}
        roots = {"/api/users": "username", "/api/projects": "name", "/api/notes": "title", "/api/files/folders": "name", "/api/chat/channels": "name"}
        if method == "POST" and path in roots:
            if isinstance(payload.get(roots[path]), str) and payload[roots[path]].startswith(self.prefix + "-"):
                return
        if method == "POST" and path == "/api/files/upload" and isinstance(filename, str) and filename.startswith(self.prefix + "-"):
            parent = payload.get("parent_id")
            if parent is None or self.owns("files", int(parent)):
                return
        if method == "POST" and path == "/api/chat/dms" and self.owns("users", payload.get("user_id")):
            return
        match = re.fullmatch(r"/api/(users|projects|tasks|notes|files|chat/messages)/(\d+)", path)
        if match:
            kind, raw_id = match.groups()
            kind = "messages" if kind == "chat/messages" else kind
            if self.owns(kind, int(raw_id)):
                if kind == "users":
                    if method == "PATCH" and set(payload) == {"active"} and type(payload["active"]) is bool:
                        return
                elif method in {"PATCH", "DELETE"}:
                    return
        match = re.fullmatch(r"/api/(projects|tasks)/(\d+)/(tasks|comments)", path)
        if method == "POST" and match and self.owns(match[1], int(match[2])) and (match[1], match[3]) in {("projects", "tasks"), ("tasks", "comments")}:
            return
        match = re.fullmatch(r"/api/chat/conversations/(\d+)/(messages|read)", path)
        if method == "POST" and match and self.owns("conversations", int(match[1])):
            if match[2] == "messages":
                return
            marker = self.rows["messages"].get(payload.get("message_id"))
            if marker and marker["conversation_id"] == int(match[1]):
                return
        raise StopRun("Refused a mutation outside confirmed namespaced fixtures: " + method + " " + path)


class Recorder:
    def __init__(self):
        self.started = time.monotonic()
        self.operations = []
        self.failures = []
        self.stage = "provision"

    def elapsed(self):
        return round(time.monotonic() - self.started, 6)

    def record(self, app, actor, method, path, kind, started, status, expected, *, error=None, size=0):
        verdict = classify(method, status, expected)
        entry = {"id": len(self.operations) + 1, "app": app, "actor": actor, "stage": self.stage, "kind": kind, "method": method, "path": path, "started_elapsed": round(started - self.started, 6), "seconds": round(time.monotonic() - started, 6), "status": status, "expected_status": list(expected), "verdict": verdict, "response_bytes": size}
        if error:
            entry["error"] = error
        self.operations.append(entry)
        if verdict in {"unexpected", "unconfirmed_write"}:
            self.failures.append(entry)
        return entry

    def summaries(self):
        grouped = defaultdict(list)
        for operation in self.operations:
            grouped[(operation["app"], operation["stage"], operation["kind"])].append(operation)
        result = []
        for (app, stage, kind), operations in sorted(grouped.items()):
            confirmed = [item["seconds"] for item in operations if item["verdict"] == "confirmed"]
            result.append({"app": app, "stage": stage, "kind": kind, "count": len(operations), "status_counts": dict(Counter(str(item["status"]) for item in operations)), "verdict_counts": dict(Counter(item["verdict"] for item in operations)), "p50": percentile(confirmed, .5), "p95": percentile(confirmed, .95), "p99": percentile(confirmed, .99), "max": max(confirmed, default=None)})
        return result


class Actor:
    def __init__(self, app, username, password, label):
        self.app, self.username, self.password, self.label = app, username, password, label
        self.client = app.client()
        self.user = None
        self.csrf = ""
        self.primary = None
        self.room = None
        self.last_message = None
        self.sequence = 0

    async def login(self):
        value = await self.app.call(self, "POST", "/api/auth/login", kind="auth", payload={"username": self.username, "password": self.password})
        user = value.get("user") if isinstance(value, dict) else None
        if not isinstance(value, dict) or not isinstance(value.get("csrf_token"), str) or not isinstance(user, dict) or user.get("username") != self.username or user.get("active") is not True or (self.user is not None and user.get("id") != self.user["id"]):
            self.app.reject_confirmation(self, "Sign-in did not confirm the requested actor identity/session.")
        if user.get("role") != ("admin" if self.label == "operator" else "member"):
            self.app.reject_confirmation(self, "Sign-in returned a different role owner.")
        self.csrf, self.user = value["csrf_token"], value["user"]
        if self.label != "operator":
            self.app.confirm("users", self.user)


class AppRun:
    def __init__(self, app_id, settings, profile, prefix, tls, recorder):
        self.app_id, self.settings, self.profile, self.prefix, self.tls, self.recorder = app_id, settings, profile, prefix, tls, recorder
        self.fixtures = Fixtures(prefix)
        self.history = []
        self.blobs = {}
        self.markers = {}
        self.conversation_members = {}
        self.conversation_observers = {}
        self.clients = []
        self.actors = []
        self.seed_gate = asyncio.Semaphore(8)
        self.operator = Actor(self, settings["username"], settings["password"], "operator")
        self.historical = None
        self.root_resource = None
        self.conflict_resource = None
        self.seed_counts = Counter()

    def client(self):
        client = httpx.AsyncClient(base_url=self.settings["url"], verify=self.tls, trust_env=False, follow_redirects=False, timeout=httpx.Timeout(10, connect=5), limits=httpx.Limits(max_connections=4, max_keepalive_connections=2, keepalive_expiry=30))
        self.clients.append(client)
        return client

    def confirm(self, kind, row):
        self.fixtures.remember(kind, row)
        self.history.append({"elapsed": self.recorder.elapsed(), "stage": self.recorder.stage, "kind": kind, "id": row["id"], "json": row})
        return row

    def reject_confirmation(self, actor, message):
        entry = actor.last_operation
        entry["verdict"] = "unconfirmed_write" if entry["method"] not in {"GET", "HEAD"} else "unexpected"
        if entry not in self.recorder.failures:
            self.recorder.failures.append(entry)
        raise StopRun(message)

    async def call(self, actor, method, path, *, kind=None, payload=None, filename=None, content=None, expected=(200,), raw=False):
        self.fixtures.admit(method, path, payload, filename)
        started = time.monotonic()
        kind = kind or ("read" if method == "GET" else "write")
        headers = {"Origin": self.settings["url"], "Accept": "application/json"}
        if actor.csrf:
            headers["X-CSRF-Token"] = actor.csrf
        arguments = {"headers": headers}
        if filename is not None:
            arguments.update(files={"file": (filename, content, "application/octet-stream")}, data={key: str(value) for key, value in (payload or {}).items() if value is not None})
        elif payload is not None:
            arguments["json"] = payload
        try:
            response = await actor.client.request(method, path, **arguments)
        except (httpx.HTTPError, asyncio.CancelledError) as exc:
            self.recorder.record(self.app_id, actor.label, method, path, kind, started, None, expected, error=type(exc).__name__)
            raise StopRun("Transport outcome is unconfirmed; no request will be replayed.") from None
        entry = self.recorder.record(self.app_id, actor.label, method, path, kind, started, response.status_code, expected, size=len(response.content))
        actor.last_operation = entry
        if entry["verdict"] in {"unexpected", "unconfirmed_write"}:
            raise StopRun(f"Unexpected {response.status_code} for {self.app_id} {method} {path}; no replay.")
        if response.status_code == 429:
            if not response.headers.get("Retry-After", "").isdigit():
                raise StopRun("A pressure rejection omitted Retry-After.")
        if raw:
            return response.content
        try:
            value = response.json()
        except ValueError:
            entry["verdict"] = "unconfirmed_write" if method != "GET" else "unexpected"
            self.recorder.failures.append(entry)
            raise StopRun("The confirmed HTTP response did not contain valid JSON; no replay.") from None
        return value

    async def create(self, actor, path, kind, payload):
        row = await self.call(actor, "POST", path, payload=payload, expected=(200, 201))
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0 or self.fixtures.owns(kind, row["id"]):
            self.reject_confirmation(actor, "Creation did not confirm a fresh resource identity.")
        identity_field = {"users": "username", "projects": "name", "tasks": "title", "notes": "title", "files": "name"}.get(kind)
        if identity_field and row.get(identity_field) != payload.get(identity_field):
            self.reject_confirmation(actor, "Creation returned another namespaced resource.")
        if any(row.get(field) != payload[field] for field in ("body", "description") if field in payload):
            self.reject_confirmation(actor, "Creation did not confirm the exact complete document content.")
        if kind in {"projects", "tasks", "notes", "files"} and row.get("created_by") != actor.user["id"]:
            self.reject_confirmation(actor, "Creation returned another author identity.")
        if kind in {"messages", "comments"} and (row.get("author_id") != actor.user["id"] or row.get("body") != payload.get("body")):
            self.reject_confirmation(actor, "Creation returned another message/comment owner.")
        parent_field = {"tasks": "project_id", "comments": "task_id", "messages": "conversation_id"}.get(kind)
        if parent_field and row.get(parent_field) != int(path.split("/")[-2]):
            self.reject_confirmation(actor, "Creation returned another parent resource identity.")
        if kind == "conversations":
            if path.endswith("/channels") and (row.get("kind") != "channel" or row.get("name") != payload.get("name")):
                self.reject_confirmation(actor, "Channel creation returned another channel identity.")
            if path.endswith("/dms") and (row.get("kind") != "dm" or not isinstance(row.get("peer"), dict) or row["peer"].get("id") != payload.get("user_id")):
                self.reject_confirmation(actor, "DM creation returned another participant identity.")
            self.conversation_observers[str(row["id"])] = actor.user["id"]
        return self.confirm(kind, row)

    async def patch(self, actor, path, kind, payload):
        row = await self.call(actor, "PATCH", path, payload=payload)
        if not isinstance(row, dict) or row.get("id") != int(path.rsplit("/", 1)[1]) or any(row.get(key) != value for key, value in payload.items() if key != "expected_revision"):
            self.reject_confirmation(actor, "An update did not confirm its exact target and requested fields.")
        if "expected_revision" in payload and row.get("revision") != payload["expected_revision"] + 1:
            self.reject_confirmation(actor, "An update did not advance the confirmed revision exactly once.")
        return self.confirm(kind, row)

    async def parallel_seed(self, count, operation):
        async def worker(offset):
            for index in range(offset, count, 8):
                await operation(index)
        async with asyncio.TaskGroup() as group:
            for offset in range(min(8, count)):
                group.create_task(worker(offset))

    async def provision(self):
        await self.operator.login()
        health = await self.call(self.operator, "GET", "/api/health", kind="setup")
        if health.get("app") != self.app_id or health.get("schema_version") != 1:
            raise StopRun("The endpoint is not the selected app/schema owner.")
        count = 100 if self.profile == "full" else 10
        password = secrets.token_urlsafe(24)
        for index in range(count + 1):
            username = f"{self.prefix}-{index:03d}"
            row = await self.create(self.operator, "/api/users", "users", {"username": username, "display_name": f"{self.prefix} actor {index}", "password": password})
            actor = Actor(self, username, password, f"actor-{index:03d}" if index < count else "historical")
            actor.user = row
            if index < count:
                self.actors.append(actor)
            else:
                self.historical = actor
        # The operator just authenticated. Start one app-local ramp immediately;
        # at most43 sign-ins/minute, including the same gateway transport peer.
        for index, actor in enumerate([*self.actors, self.historical]):
            await asyncio.sleep(AUTH_INTERVAL)
            await actor.login()
            if (index + 1) % 10 == 0:
                progress("auth_ramp", app=self.app_id, authenticated=index + 1)

    def bytes_for(self, index):
        return random.Random(f"{self.prefix}-{self.app_id}-{index}").randbytes(MIB)

    async def upload(self, actor, index):
        payload = self.bytes_for(index)
        name = f"{self.prefix}-{index:06d}.bin"
        row = await self.call(actor, "POST", "/api/files/upload", filename=name, content=payload, payload={"parent_id": self.root_resource["id"]}, kind="transfer", expected=(201,))
        if not isinstance(row, dict) or type(row.get("id")) is not int or self.fixtures.owns("files", row["id"]) or row.get("size") != len(payload) or row.get("created_by") != actor.user["id"] or row.get("name") != name or row.get("kind") != "file" or row.get("parent_id") != self.root_resource["id"]:
            self.reject_confirmation(actor, "Upload identity/size/authorship did not match the confirmed producer.")
        self.blobs[str(row["id"])] = {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "source_index": index}
        return self.confirm("files", row)

    async def seed(self):
        count = SEEDS[self.profile]
        history = self.historical
        if self.app_id == "projects":
            self.root_resource = await self.create(history, "/api/projects", "projects", {"name": self.prefix + "-project", "description": "Workload source-owned project"})
            archived = await self.create(history, "/api/projects", "projects", {"name": self.prefix + "-archived", "description": "Historical project"})
            await self.patch(history, f"/api/projects/{archived['id']}", "projects", {"archived": True})
            async def task(index):
                actor = history if index < 5 else self.actors[index % len(self.actors)]
                row = await self.create(actor, f"/api/projects/{self.root_resource['id']}/tasks", "tasks", {"title": f"{self.prefix}-task-{index:05d}", "description": document_text("projects", f"{self.prefix} seeded task {index:05d}"), "status": CATALOG["task_statuses"][index % len(CATALOG["task_statuses"])]["id"], "priority": CATALOG["task_priorities"][(index // len(CATALOG["task_statuses"])) % len(CATALOG["task_priorities"])]["id"], "assignee_id": actor.user["id"], "due_date": "2030-01-15"})
                if index < 5:
                    await self.patch(actor, f"/api/tasks/{row['id']}", "tasks", {"archived": True, "expected_revision": row["revision"]})
                elif actor.primary is None:
                    actor.primary = row["id"]
            await self.parallel_seed(count["tasks"], task)
            self.conflict_resource = await self.create(self.actors[0], f"/api/projects/{self.root_resource['id']}/tasks", "tasks", {"title": self.prefix + "-conflict", "description": document_text("projects", self.prefix + " controlled task conflict")})
            await self.create(history, f"/api/tasks/{self.conflict_resource['id']}/comments", "comments", {"body": self.prefix + " retained authorship"})
            self.seed_counts["tasks"] = count["tasks"] + 1
        elif self.app_id == "notes":
            async def note(index):
                actor = history if index < 5 else self.actors[index % len(self.actors)]
                row = await self.create(actor, "/api/notes", "notes", {"title": f"{self.prefix}-note-{index:05d}", "body": document_text("notes", f"{self.prefix} seeded note {index:05d} 🌍")})
                if index < 5:
                    await self.patch(actor, f"/api/notes/{row['id']}", "notes", {"archived": True, "expected_revision": row["revision"]})
                elif actor.primary is None:
                    actor.primary = row["id"]
            await self.parallel_seed(count["notes"], note)
            self.conflict_resource = await self.create(self.actors[0], "/api/notes", "notes", {"title": self.prefix + "-conflict", "body": document_text("notes", self.prefix + " controlled note conflict")})
            self.seed_counts["notes"] = count["notes"] + 1
        elif self.app_id == "files":
            self.root_resource = await self.create(history, "/api/files/folders", "files", {"name": self.prefix + "-files", "parent_id": None})
            async def file(index):
                actor = history if index < 5 else self.actors[index % len(self.actors)]
                row = await self.upload(actor, index)
                if index < 5:
                    await self.call(actor, "DELETE", f"/api/files/{row['id']}")
                elif actor.primary is None:
                    actor.primary = row["id"]
            # Every smoke actor needs one active real file as well as retained
            # history; the full200 files naturally cover all100 actors.
            await self.parallel_seed(max(count["files"], len(self.actors) + 5), file)
            trash = await self.call(self.operator, "GET", f"/api/files?trash=true&q={self.prefix}", kind="setup")
            for row in trash["items"]:
                if not self.fixtures.owns("files", row["id"]):
                    raise StopRun("Trash returned an unowned fixture identity.")
                self.confirm("files", row)
            self.seed_counts["files"] = max(count["files"], len(self.actors) + 5)
        else:
            channels = [await self.create(history, "/api/chat/channels", "conversations", {"name": f"{self.prefix}-channel-{index}"}) for index in range(5)]
            for index in range(0, len(self.actors), 2):
                first, second = self.actors[index:index + 2]
                direct = await self.create(first, "/api/chat/dms", "conversations", {"user_id": second.user["id"]})
                first.room = second.room = direct["id"]
                self.conversation_members[str(direct["id"])] = [first.user["id"], second.user["id"]]
            old_dm = await self.create(history, "/api/chat/dms", "conversations", {"user_id": self.actors[0].user["id"]})
            self.conversation_members[str(old_dm["id"])] = [history.user["id"], self.actors[0].user["id"]]
            self.old_dm = old_dm["id"]
            self.channels = [row["id"] for row in channels]
            async def message(index):
                actor = history if index < 10 else self.actors[index % len(self.actors)]
                room = (self.old_dm if index % 2 == 0 else self.channels[index % 5]) if actor is history else (actor.room if index % 5 == 0 else self.channels[index % 5])
                row = await self.create(actor, f"/api/chat/conversations/{room}/messages", "messages", {"body": f"{self.prefix} seeded message {index:06d} 🌍"})
                if index < 5:
                    await self.call(actor, "DELETE", f"/api/chat/messages/{row['id']}")
                    snapshot = await self.call(actor, "GET", f"/api/chat/conversations/{room}/messages?before_id={row['id'] + 1}", kind="setup")
                    self.confirm("messages", next(item for item in snapshot["items"] if item["id"] == row["id"]))
                elif index < 10:
                    await self.patch(actor, f"/api/chat/messages/{row['id']}", "messages", {"body": f"{self.prefix} edited historical message {index}"})
                else:
                    actor.last_message = row["id"]
            await self.parallel_seed(count["messages"], message)
            self.seed_counts["messages"] = count["messages"]
        await self.patch(self.operator, f"/api/users/{history.user['id']}", "users", {"active": False})
        if any(actor.primary is None for actor in self.actors) and self.app_id != "chat":
            raise StopRun("Seeding did not give every workload actor its own active resource.")
        await self.controls()

    async def controls(self):
        if self.app_id in {"projects", "notes"}:
            kind = "tasks" if self.app_id == "projects" else "notes"
            path = f"/api/{kind}/{self.conflict_resource['id']}"
            current = await self.call(self.actors[0], "GET", path)
            async def edit(actor, index):
                payload = {"expected_revision": current["revision"], "description" if kind == "tasks" else "body": document_text(self.app_id, f"{self.prefix} controlled {self.recorder.stage} {index}")}
                value = await self.call(actor, "PATCH", path, payload=payload, expected=(200, 409))
                if actor.last_operation["status"] == 200:
                    field = "description" if kind == "tasks" else "body"
                    if not isinstance(value, dict) or value.get("id") != current["id"] or value.get("revision") != current["revision"] + 1 or value.get(field) != payload[field]:
                        self.reject_confirmation(actor, "A conflicting edit did not confirm its target/revision/content.")
                    self.confirm(kind, value)
                    return 200
                return 409
            if sorted(await asyncio.gather(edit(self.actors[0], 0), edit(self.actors[1], 1))) != [200, 409]:
                raise StopRun("Exactly one competing revision must commit and one must reject409.")
        elif self.app_id == "chat":
            room = self.actors[0].room
            for actor in (self.operator, self.actors[2]):
                for suffix in ("", "/messages"):
                    await self.call(actor, "GET", f"/api/chat/conversations/{room}{suffix}", expected=(404,))
            await self.call(self.actors[0], "POST", f"/api/chat/conversations/{self.old_dm}/messages", payload={"body": self.prefix + " rejected disabled peer"}, expected=(409,))

    async def action(self, actor, rng):
        actor.sequence += 1
        if self.app_id in {"projects", "notes"}:
            kind = "tasks" if self.app_id == "projects" else "notes"
            row = self.fixtures.rows[kind][actor.primary]
            if rng.random() < .3:
                field = "description" if kind == "tasks" else "body"
                await self.patch(actor, f"/api/{kind}/{row['id']}", kind, {field: document_text(self.app_id, f"{self.prefix} {actor.label} edit {actor.sequence} 🌍"), "expected_revision": row["revision"]})
            elif rng.random() < .7:
                value = await self.call(actor, "GET", f"/api/{kind}/{row['id']}")
                if value != row:
                    raise StopRun("A read did not reproduce the last confirmed revision/content.")
            else:
                path = f"/api/projects/{self.root_resource['id']}/tasks" if kind == "tasks" else "/api/notes"
                await self.call(actor, "GET", path)
        elif self.app_id == "files":
            row = self.fixtures.rows["files"][actor.primary]
            choice = rng.random()
            if choice < .15:
                await self.call(actor, "GET", f"/api/files?parent_id={self.root_resource['id']}")
            elif choice < .7:
                content = await self.call(actor, "GET", f"/api/files/{row['id']}/download", kind="transfer", raw=True)
                proof = self.blobs[str(row["id"])]
                if len(content) != proof["size"] or hashlib.sha256(content).hexdigest() != proof["sha256"]:
                    raise StopRun("Downloaded bytes differ from the confirmed1MiB blob hash.")
            elif choice < .9:
                await self.patch(actor, f"/api/files/{row['id']}", "files", {"name": f"{self.prefix}-{actor.label}-{actor.sequence:06d}.bin"})
            else:
                index = 1000000 + self.actors.index(actor) * 10000 + actor.sequence
                await self.upload(actor, index)
        else:
            room = actor.room if rng.random() < .5 else self.channels[self.actors.index(actor) % len(self.channels)]
            page = await self.call(actor, "GET", f"/api/chat/conversations/{room}/messages")
            if actor.sequence % 10 == 0:
                row = await self.create(actor, f"/api/chat/conversations/{room}/messages", "messages", {"body": f"{self.prefix} {actor.label} message {actor.sequence:06d}"})
                actor.last_message = row["id"]
            elif actor.sequence % 17 == 0 and actor.last_message:
                await self.patch(actor, f"/api/chat/messages/{actor.last_message}", "messages", {"body": f"{self.prefix} {actor.label} edited {actor.sequence:06d}"})
            elif page["items"] and actor.sequence % 5 == 0:
                row = page["items"][-1]
                if not self.fixtures.owns("messages", row["id"]):
                    if not self.fixtures.owns("users", row["author_id"]) or row["conversation_id"] != room:
                        raise StopRun("An unowned message appeared in the private workload fixture.")
                    self.confirm("messages", row)
                await self.call(actor, "POST", f"/api/chat/conversations/{room}/read", payload={"message_id": row["id"]})
                key = f"{room}:{actor.user['id']}"
                self.markers[key] = max(self.markers.get(key, 0), row["id"])

    async def run_stage(self, stage, deadline):
        async def worker(index):
            actor = self.actors[index]
            rng = random.Random(f"{self.prefix}-{self.app_id}-{stage.name}-{index}")
            await asyncio.sleep(rng.uniform(0, 2.5))
            while time.monotonic() < deadline:
                started = time.monotonic()
                await self.action(actor, rng)
                delay = max(0, 2.5 - (time.monotonic() - started)) if self.app_id == "chat" else rng.uniform(3, 8)
                await asyncio.sleep(min(delay, max(0, deadline - time.monotonic())))
        async with asyncio.TaskGroup() as group:
            for index in range(stage.users):
                group.create_task(worker(index))

    async def audit(self):
        if self.app_id in {"notes", "projects"}:
            kind = "notes" if self.app_id == "notes" else "tasks"
            rows = list(self.fixtures.rows[kind].values())
            async def check(index):
                expected = rows[index]
                actual = await self.call(self.actors[0], "GET", f"/api/{kind}/{expected['id']}", kind="audit")
                if actual != expected:
                    raise StopRun("Final content/revision/authorship audit failed.")
            await self.parallel_seed(len(rows), check)
        elif self.app_id == "files":
            rows = list(self.fixtures.rows["files"].values())
            async def check(index):
                expected = rows[index]
                if expected["deleted_at"] is not None:
                    return
                actual = await self.call(self.actors[0], "GET", f"/api/files/{expected['id']}", kind="audit")
                if actual != expected:
                    raise StopRun("Final Files metadata audit failed.")
                if expected["kind"] == "file":
                    content = await self.call(self.actors[0], "GET", f"/api/files/{expected['id']}/download", kind="audit", raw=True)
                    proof = self.blobs[str(expected["id"])]
                    if len(content) != proof["size"] or hashlib.sha256(content).hexdigest() != proof["sha256"]:
                        raise StopRun("Final Files byte hash audit failed.")
            await self.parallel_seed(len(rows), check)
        else:
            for room in self.fixtures.rows["conversations"]:
                members = self.conversation_members.get(str(room))
                actor = next((item for item in self.actors if members is None or item.user["id"] in members), None)
                before, seen = None, set()
                while True:
                    suffix = "" if before is None else f"?before_id={before}"
                    page = await self.call(actor, "GET", f"/api/chat/conversations/{room}/messages{suffix}", kind="audit")
                    for row in page["items"]:
                        if self.fixtures.rows["messages"].get(row["id"]) != row:
                            raise StopRun("Final Chat text/tombstone/authorship audit failed.")
                        seen.add(row["id"])
                    if not page["has_more"]:
                        break
                    before = page["items"][0]["id"]
                if seen != {row["id"] for row in self.fixtures.rows["messages"].values() if row["conversation_id"] == room}:
                    raise StopRun("Final Chat history count audit failed.")
                detail = await self.call(actor, "GET", f"/api/chat/conversations/{room}", kind="audit")
                marker = self.markers.get(f"{room}:{actor.user['id']}", 0)
                unread = sum(row["conversation_id"] == room and not row["deleted"] and row["author_id"] != actor.user["id"] and row["id"] > marker for row in self.fixtures.rows["messages"].values())
                if detail.get("id") != room or detail.get("unread_count") != unread:
                    raise StopRun("Final conversation/read-marker owner audit failed.")
                self.conversation_observers[str(room)] = actor.user["id"]
                self.confirm("conversations", detail)

    async def pressure(self):
        async def one(index):
            actor = Actor(self, self.prefix + "-unknown", secrets.token_urlsafe(24), f"new-connection-{index}")
            await self.call(actor, "POST", "/api/auth/login", kind="auth_pressure", payload={"username": actor.username, "password": actor.password}, expected=(401, 429))
        await asyncio.gather(*(one(index) for index in range(16)))

    async def connection_baseline(self):
        async def one(index):
            actor = Actor(self, "", "", f"new-connection-read-{index}")
            value = await self.call(actor, "GET", "/api/health", kind="connection_read")
            if value.get("app") != self.app_id:
                raise StopRun("A fresh TLS connection reached the wrong app.")
        await asyncio.gather(*(one(index) for index in range(8)))

    def oracle(self):
        documents = self.fixtures.rows["notes" if self.app_id == "notes" else "tasks"] if self.app_id in DOCUMENT_BYTES else {}
        field = "body" if self.app_id == "notes" else "description"
        sizes = [len(row[field].encode()) for row in documents.values()]
        return {
            "resources": {kind: {str(identity): row for identity, row in rows.items()} for kind, rows in self.fixtures.rows.items()},
            "blobs": self.blobs,
            "read_markers": self.markers,
            "conversation_members": self.conversation_members,
            "conversation_observers": self.conversation_observers,
            "confirmed_history": self.history,
            "seed_counts": dict(self.seed_counts),
            "document_utf8_bytes": {"count": len(sizes), "minimum": min(sizes, default=None), "maximum": max(sizes, default=None)} if self.app_id in DOCUMENT_BYTES else None,
            "operator_id": self.operator.user["id"] if self.operator.user else None,
        }

    async def close(self):
        await asyncio.gather(*(client.aclose() for client in self.clients))


def progress(event, **fields):
    print(json.dumps({"event": event, "utc": utc(), "pid": os.getpid(), **fields}), flush=True)


class Report:
    def __init__(self, path):
        self.path = Path(path)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)

    def write(self, value):
        temporary = self.path.with_name(self.path.name + "." + secrets.token_hex(4) + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w") as target:
                json.dump(value, target, ensure_ascii=False, separators=(",", ":"))
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


def verdict(recorder, profile, completed, recovery_seconds):
    failures = []
    measured = {stage.name for stage in STAGES[profile]}
    for summary in recorder.summaries():
        if summary["kind"] == "auth" or summary["stage"] in measured:
            for quantile, maximum in BUDGETS.get(summary["kind"], {}).items():
                if summary[quantile] is not None and summary[quantile] > maximum:
                    failures.append({"app": summary["app"], "stage": summary["stage"], "kind": summary["kind"], "quantile": quantile, "actual": summary[quantile], "maximum": maximum})
    if completed:
        for app in APPS:
            for stage in measured:
                required = {"read", "write", "transfer"} if app == "files" else {"read", "write"}
                present = {item["kind"] for item in recorder.operations if item["app"] == app and item["stage"] == stage and item["verdict"] == "confirmed"}
                if not required <= present:
                    failures.append({"app": app, "stage": stage, "missing_operation_kinds": sorted(required - present)})
    if recovery_seconds is not None and recovery_seconds > 30:
        failures.append({"kind": "recovery", "actual": recovery_seconds, "maximum": 30})
    passed = completed and not recorder.failures and not failures
    return {"passed": passed, "capacity_evidence": profile == "full" and passed, "profile_scope": "Full predeclared workload" if profile == "full" else "30-second diagnostics only; not capacity evidence", "budget_failures": failures, "unexpected_operations": len(recorder.failures)}


async def run(arguments):
    settings = private_config(arguments.config)
    tls = ssl.create_default_context(cafile=arguments.ca_file)
    prefix = "wl-" + secrets.token_hex(5)
    output = Report(arguments.output)
    recorder = Recorder()
    apps = [AppRun(app, settings[app], arguments.profile, prefix, tls, recorder) for app in APPS]
    cpu_start = resource.getrusage(resource.RUSAGE_SELF)
    runner_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    started_utc, stages, completed, failure, recovery_seconds = utc(), [], False, None, None

    def report():
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return {
            "format": 1,
            "namespace": prefix,
            "profile": arguments.profile,
            "model": "OPENSW-PRODUCTION-R11",
            "runner_sha256": runner_digest,
            "started_utc": started_utc,
            "updated_utc": utc(),
            "elapsed_seconds": recorder.elapsed(),
            "client": {
                "pid": os.getpid(),
                "python": sys.version.split()[0],
                "user_cpu_seconds": usage.ru_utime - cpu_start.ru_utime,
                "system_cpu_seconds": usage.ru_stime - cpu_start.ru_stime,
                "peak_rss_bytes": usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024,
                "httpx": httpx.__version__,
                "tls_verification": "normal hostname/certificate verification with explicit CA bundle" if arguments.ca_file else "normal hostname/certificate verification with system CA roots",
                "persistent_client_per_actor": True,
                "auth_interval_seconds_per_app": AUTH_INTERVAL,
            },
            "targets": {app: item["url"] for app, item in settings.items()},
            "stages": stages,
            "budgets_seconds": BUDGETS,
            "document_payload_targets_utf8_bytes": {"notes.body": DOCUMENT_BYTES["notes"], "tasks.description": DOCUMENT_BYTES["projects"]},
            "recovery_seconds": recovery_seconds,
            "result": verdict(recorder, arguments.profile, completed, recovery_seconds),
            "failure": failure,
            "latencies": recorder.summaries(),
            "operations": recorder.operations,
            "oracle_semantics": "Latest complete confirmed resource responses and every confirmed mutation version. Conversation name/peer/unread_count are observer-derived; explicit observer and member IDs are separate. All fixture data retained; provided operator is never modified.",
            "oracle": {app.app_id: app.oracle() for app in apps},
        }
    try:
        progress("provision_start", namespace=prefix, profile=arguments.profile, output=str(output.path))
        async with asyncio.TaskGroup() as group:
            for app in apps:
                group.create_task(app.provision())
        recorder.stage = "seed"
        progress("seed_start", counts=SEEDS[arguments.profile])
        async with asyncio.TaskGroup() as group:
            for app in apps:
                group.create_task(app.seed())
        output.write(report())
        for stage in STAGES[arguments.profile]:
            recorder.stage = stage.name
            # Deterministic conflict/privacy controls complete before the stage
            # workers; their explicit409/404s have separate verdict counts.
            async with asyncio.TaskGroup() as group:
                for app in apps:
                    group.create_task(app.controls())
            start = time.monotonic()
            stage_cpu = resource.getrusage(resource.RUSAGE_SELF)
            deadline = start + stage.seconds
            progress("stage_start", stage=stage.name, users_per_app=stage.users, duration_seconds=stage.seconds)
            stages.append({"name": stage.name, "users_per_app": stage.users, "planned_seconds": stage.seconds, "started_utc": utc(), "started_elapsed": recorder.elapsed()})
            async with asyncio.TaskGroup() as group:
                for app in apps:
                    group.create_task(app.run_stage(stage, deadline))
            end_cpu = resource.getrusage(resource.RUSAGE_SELF)
            stages[-1].update(actual_seconds=round(time.monotonic() - start, 6), ended_utc=utc(), client_user_cpu_seconds=end_cpu.ru_utime - stage_cpu.ru_utime, client_system_cpu_seconds=end_cpu.ru_stime - stage_cpu.ru_stime)
            if stage.name == "recovery10":
                probes = [item for item in recorder.operations if item["stage"] == stage.name and item["started_elapsed"] >= stages[-1]["started_elapsed"] and item["verdict"] == "confirmed" and item["kind"] in {"read", "write", "transfer"}]
                first_by_app = {app: min((item["started_elapsed"] + item["seconds"] for item in probes if item["app"] == app), default=float("inf")) for app in APPS}
                recovery_seconds = max(first_by_app.values()) - stages[-1]["started_elapsed"]
            progress("stage_end", stage=stage.name, operations=len(recorder.operations))
            output.write(report())
        recorder.stage = "audit"
        progress("audit_start")
        async with asyncio.TaskGroup() as group:
            for app in apps:
                group.create_task(app.audit())
        recorder.stage = "new_connection_baseline"
        progress("new_connection_baseline_start")
        async with asyncio.TaskGroup() as group:
            for app in apps:
                group.create_task(app.connection_baseline())
        recorder.stage = "new_connection_auth_pressure"
        progress("auth_pressure_start", legitimate_authentication_complete=True)
        async with asyncio.TaskGroup() as group:
            for app in apps:
                group.create_task(app.pressure())
        completed = True
    except (Exception, asyncio.CancelledError) as exc:
        # Never include exception input/body/headers: credentials and cookies
        # are held only in the private in-memory actors.
        def safe_causes(error):
            if isinstance(error, BaseExceptionGroup):
                return [item for child in error.exceptions for item in safe_causes(child)]
            return [str(error) if isinstance(error, StopRun) else type(error).__name__]
        failure = {"type": type(exc).__name__, "stage": recorder.stage, "message": "Run stopped; failed or uncertain operations were not replayed.", "causes": safe_causes(exc)[:10]}
    finally:
        await asyncio.gather(*(app.close() for app in apps))
        final = report()
        output.write(final)
        progress("run_end", namespace=prefix, passed=final["result"]["passed"], capacity_evidence=final["result"]["capacity_evidence"], report=str(output.path))
    return 0 if final["result"]["passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--profile", choices=("full", "smoke"), default="full")
    arguments = parser.parse_args(argv)
    try:
        return asyncio.run(run(arguments))
    except (OSError, ValueError) as exc:
        print("Workload prerequisite rejected: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
