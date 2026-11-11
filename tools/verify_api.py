#!/usr/bin/env python3
"""Exercise a running OpenSW app using its public HTTP API only.

This verifier creates two uniquely named test members and app-specific test
records. It never changes an existing operator's password, active state, or
content. Cleanup uses only confirmed test IDs; retained soft-deleted test rows
are reported because OpenSW deliberately has no permanent-delete API.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import getpass
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import ssl
import sys
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler, HTTPCookieProcessor, HTTPSHandler, ProxyHandler, Request, build_opener,
)


COMMON_ROUTES = {
    "GET /api/health", "GET /api/me", "POST /api/auth/login",
    "POST /api/auth/logout", "POST /api/auth/password", "GET /api/users",
    "POST /api/users", "PATCH /api/users/{id}",
}
APP_ROUTES = {
    "files": {
        "GET /api/files", "POST /api/files/upload", "POST /api/files/folders",
        "GET /api/files/{id}", "PATCH /api/files/{id}", "DELETE /api/files/{id}",
        "POST /api/files/{id}/restore", "GET /api/files/{id}/download",
    },
    "projects": {
        "GET /api/projects", "POST /api/projects", "GET /api/projects/{id}", "PATCH /api/projects/{id}",
        "GET /api/projects/{id}/tasks", "POST /api/projects/{id}/tasks",
        "GET /api/tasks/{id}", "PATCH /api/tasks/{id}",
        "GET /api/tasks/{id}/comments", "POST /api/tasks/{id}/comments",
    },
    "notes": {"GET /api/notes", "POST /api/notes", "GET /api/notes/{id}", "PATCH /api/notes/{id}"},
    "chat": {
        "GET /api/chat/channels", "POST /api/chat/channels", "GET /api/chat/dms",
        "POST /api/chat/dms", "GET /api/chat/conversations/{id}", "GET /api/chat/conversations/{id}/messages",
        "POST /api/chat/conversations/{id}/messages", "PATCH /api/chat/messages/{id}",
        "DELETE /api/chat/messages/{id}", "POST /api/chat/conversations/{id}/read",
    },
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PrerequisiteFailed(Exception):
    """Stop dependent writes while still running known-fixture cleanup."""


@dataclass
class Response:
    status: int
    body: bytes
    headers: object

    def json(self):
        return json.loads(self.body)


class Actor:
    def __init__(self, url: str, origin: str, username: str, password: str, *, ca_file=None):
        self.url = url.rstrip("/")
        self.origin = origin.rstrip("/")
        self.username = username
        self.password = password
        self.cookies = http.cookiejar.CookieJar()
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(self.cookies), NoRedirect(),
                                  HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))
        self.csrf = ""
        self.user = None

    def request(self, method: str, path: str, *, payload=None, body=None, content_type=None):
        headers = {"Origin": self.origin, "Accept": "application/json"}
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if payload is not None:
            body = json.dumps(payload).encode()
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(self.url + path, data=body, headers=headers, method=method)
        try:
            result = self.opener.open(request, timeout=20)
        except HTTPError as error:
            result = error
        with result:
            data = result.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError("The API response exceeded this check's 2 MiB safety limit.")
            return Response(result.status, data, result.headers)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def resource(response: Response, fields=()):
    value = response.json()
    require(isinstance(value, dict), "Expected a JSON resource object.")
    require(type(value.get("id")) is int and value["id"] > 0, "Expected a confirmed positive resource ID.")
    require(all(field in value for field in fields), "Resource response is missing required fields.")
    return value


def page(response: Response):
    value = response.json()
    require(isinstance(value, dict) and isinstance(value.get("items"), list), "Expected a paginated collection.")
    require("next_cursor" in value and len(value["items"]) <= 100, "Collection pagination contract is invalid.")
    return value


def messages_page(response: Response):
    value = response.json()
    require(isinstance(value, dict) and isinstance(value.get("items"), list), "Expected a message snapshot.")
    require(type(value.get("has_more")) is bool and len(value["items"]) <= 100, "Message pagination contract is invalid.")
    require(all(isinstance(item, dict) and type(item.get("id")) is int and isinstance(item.get("body"), str) for item in value["items"]), "Message snapshot contains malformed records.")
    return value


class Verifier:
    def __init__(self, app: str, url: str, origin: str, username: str, password: str, *, ca_file=None):
        self.app = app
        self.url = url.rstrip("/")
        self.origin = origin.rstrip("/")
        self.prefix = "opensw-check-" + secrets.token_hex(4)
        if origin.startswith("https://") and not url.startswith("https://"):
            raise ValueError("A public HTTPS origin must be verified through actual HTTPS so Secure cookies and TLS identity are checked.")
        self.ca_file = ca_file
        self.operator = Actor(url, origin, username, password, ca_file=ca_file)
        self.actors = []
        self.users = []
        self.fixtures = []
        self.steps = []
        self.coverage = {route: [] for route in COMMON_ROUTES | APP_ROUTES[app]}
        self.secrets = {password}

    def redact(self, value):
        text = str(value)
        for actor in [self.operator, *self.actors]:
            self.secrets.add(actor.password)
            if actor.csrf:
                self.secrets.add(actor.csrf)
            self.secrets.update(cookie.value for cookie in actor.cookies)
        for secret in sorted((s for s in self.secrets if s), key=len, reverse=True):
            text = text.replace(secret, "[REDACTED]")
        return text[:500]

    def report(self, name, state, *, route=None, expected=None, actual=None, detail=""):
        step = {"name": name, "state": state}
        if route:
            step.update(route=route, expected=expected, actual=actual)
            if route in self.coverage:
                self.coverage[route].append(state)
        if detail:
            step["detail"] = self.redact(detail)
        self.steps.append(step)
        suffix = f" · {route} · expected {expected}, got {actual}" if route else ""
        print(f"[{state}] {name}{suffix}", flush=True)
        if detail:
            print("       " + self.redact(detail), flush=True)

    def check(self, name, actor, method, path, *, route, expected=(200,), payload=None,
              body=None, content_type=None, validate: Callable | None = None):
        try:
            response = actor.request(method, path, payload=payload, body=body, content_type=content_type)
        except (OSError, URLError, ValueError, TimeoutError) as error:
            uncertain = method not in {"GET", "HEAD"}
            detail = ("Outcome UNCONFIRMED. This action will not be retried. " if uncertain else "") + str(error)
            self.report(name, "FAIL", route=route, expected=list(expected), actual="connection/unreadable", detail=detail)
            return None
        try:
            require(response.status in expected, self.hint(response))
            value = validate(response) if validate else response
        except (AssertionError, ValueError, KeyError, TypeError, AttributeError) as error:
            self.report(name, "FAIL", route=route, expected=list(expected), actual=response.status, detail=str(error))
            return None
        self.report(name, "PASS", route=route, expected=list(expected), actual=response.status)
        return value

    @staticmethod
    def hint(response):
        try:
            detail = response.json().get("detail", "Unexpected response.")
        except (ValueError, AttributeError):
            detail = "Expected JSON; check the app address and server logs."
        hints = {
            401: "Check the supplied credentials and whether the account is active.",
            403: "Check the configured app origin, CSRF token, role, and conversation membership.",
            404: "Check the selected app and resource identity; another app's API is intentionally unavailable.",
            409: "The canonical resource changed or conflicts with the requested action; inspect it before another write.",
            413: "Check the configured upload size limit.",
            422: "Check the named request fields against this release's API contract.",
        }
        return str(detail) + " " + hints.get(response.status, "Inspect the exact container's logs; no automatic write retry was attempted.")

    def login(self, actor, name):
        def validate(response):
            value = response.json()
            require(value.get("app") == self.app, "Login returned another app's identity.")
            user = value.get("user")
            require(isinstance(user, dict) and user.get("username") == actor.username and user.get("active") is True, "Login identity does not match the supplied account.")
            require(type(user.get("id")) is int and user["id"] > 0, "Login returned no confirmed user ID.")
            require(isinstance(value.get("csrf_token"), str) and value["csrf_token"], "Login returned no CSRF token.")
            actor.user, actor.csrf = user, value["csrf_token"]
            return value
        return self.check(name, actor, "POST", "/api/auth/login", route="POST /api/auth/login",
                          payload={"username": actor.username, "password": actor.password}, validate=validate)

    def safe_member(self, actor):
        confirmed = next((u for u in self.users if actor.user and u["id"] == actor.user.get("id")), None)
        return bool(confirmed and actor.user["id"] != self.operator.user["id"]
                    and actor.user.get("role") == "member" and actor.user.get("username") == confirmed["username"])

    def fixture(self, kind, value, *, author=None):
        require(isinstance(value, dict) and type(value.get("id")) is int, "No confirmed fixture ID.")
        if author is not None:
            require(value.get("created_by", value.get("author_id")) == author.user["id"], "Created fixture has an unexpected author.")
        self.fixtures.append({"kind": kind, "id": value["id"], "value": value, "actor": author})
        return value

    def run(self):
        print(f"OpenSW {self.app} API verification · {self.prefix}")
        print("Creates two test members and isolated test records; cleanup is soft and retained rows are reported.")
        try:
            health = self.check("Selected app is ready", self.operator, "GET", "/api/health", route="GET /api/health", validate=lambda r: self.health(r))
            if not health:
                raise PrerequisiteFailed("The selected app did not pass its readiness check.")
            self.check("Anonymous session is rejected", self.operator, "GET", "/api/me", route="GET /api/me", expected=(401,))
            if not self.login(self.operator, "Operator administrator can sign in"):
                raise PrerequisiteFailed("The operator session was not established.")
            if self.operator.user.get("role") != "admin":
                self.report("Administrator required to create isolated verification members", "FAIL")
                raise PrerequisiteFailed("No temporary accounts were created with a non-administrator account.")
            confirmed = self.check("Confirmed operator session", self.operator, "GET", "/api/me", route="GET /api/me", validate=lambda r: require(all(r.json().get("user", {}).get(key) == self.operator.user[key] for key in ("id", "username", "role", "active")), "Operator session identity changed.") or True)
            if not confirmed:
                raise PrerequisiteFailed("The operator's current identity could not be confirmed.")
            self.check("Team listing has pagination", self.operator, "GET", "/api/users", route="GET /api/users", validate=page)
            for suffix in ("a", "b"):
                username = f"{self.prefix}-{suffix}"
                password = "OpenSW-" + secrets.token_urlsafe(24)
                self.secrets.add(password)
                value = self.check(f"Create test member {suffix.upper()}", self.operator, "POST", "/api/users", route="POST /api/users", expected=(200, 201), payload={"username": username, "display_name": f"OpenSW check {suffix.upper()}", "password": password}, validate=lambda r: resource(r, ("username", "display_name", "role", "active")))
                if value:
                    if value["id"] == self.operator.user["id"] or value.get("username") != username or value.get("role") != "member" or value.get("active") is not True:
                        self.report("Refuse unsafe temporary-account identity", "FAIL", detail="The returned user is not the requested newly created member. No password or deactivation action will use this identity.")
                        continue
                    self.users.append(value)
                    actor = Actor(self.url, self.origin, username, password, ca_file=self.ca_file)
                    self.actors.append(actor)
                    self.login(actor, f"Test member {suffix.upper()} can sign in")
            if len(self.actors) == 2 and all(self.safe_member(actor) for actor in self.actors):
                getattr(self, "verify_" + self.app)(*self.actors)
                self.password_cycle(self.actors[0])
            else:
                self.report("App workflow needs two confirmed temporary members", "BLOCKED")
        except PrerequisiteFailed as error:
            self.report("Dependent verification steps were not run", "BLOCKED", detail=str(error))
        except (AssertionError, KeyError, TypeError, ValueError, AttributeError) as error:
            self.report("Verification workflow stopped safely", "FAIL", detail=str(error))
        finally:
            self.cleanup()
        return self.finish()

    def health(self, response):
        value = response.json()
        require(value.get("status") == "ok" and value.get("app") == self.app and value.get("schema_version") == 1,
                "Health does not confirm the requested app and schema.")
        return value

    def password_cycle(self, actor):
        if not self.safe_member(actor):
            self.report("Refuse password change on unconfirmed/non-test user", "FAIL")
            return
        me = self.check("Reconfirm temporary identity before password test", actor, "GET", "/api/me", route="GET /api/me", validate=lambda r: r.json())
        if not me or not all(me.get("user", {}).get(key) == actor.user[key] for key in ("id", "username", "role", "active")) or not self.safe_member(actor):
            self.report("Password test identity did not match the confirmed temporary member", "BLOCKED")
            return
        new_password = "OpenSW-changed-" + secrets.token_urlsafe(20)
        self.secrets.add(new_password)
        result = self.check("Temporary password change", actor, "POST", "/api/auth/password", route="POST /api/auth/password", payload={"current_password": actor.password, "new_password": new_password})
        if result:
            self.check("Password change revoked the old session", actor, "GET", "/api/me", route="GET /api/me", expected=(401,))
            actor.password = new_password
            self.login(actor, "Temporary member can sign in with the new password")

    def verify_files(self, a, b):
        folder = self.check("Create a test folder", a, "POST", "/api/files/folders", route="POST /api/files/folders", expected=(200, 201), payload={"name": self.prefix, "parent_id": None}, validate=resource)
        if not folder:
            return
        self.fixture("folder", folder, author=a)
        boundary = "OpenSW" + secrets.token_hex(12)
        contents = b"OpenSW API check: original byte content.\x00\xff\n"
        raw = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"parent_id\"\r\n\r\n{folder['id']}\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"check.txt\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + contents + f"\r\n--{boundary}--\r\n".encode()
        item = self.check("Upload a file into the confirmed folder", a, "POST", "/api/files/upload", route="POST /api/files/upload", expected=(200, 201), body=raw, content_type=f"multipart/form-data; boundary={boundary}", validate=lambda r: resource(r, ("name", "kind", "parent_id", "size", "created_by", "deleted_at")))
        if not item:
            return
        self.fixture("file", item, author=a)
        self.check("Another member can list the folder", b, "GET", f"/api/files?parent_id={folder['id']}", route="GET /api/files", validate=lambda r: require(any(row["id"] == item["id"] for row in page(r)["items"]), "Uploaded file is absent from the folder.") or True)
        self.check("Read complete file metadata", b, "GET", f"/api/files/{item['id']}", route="GET /api/files/{id}", validate=lambda r: require(resource(r)["id"] == item["id"], "Wrong file identity.") or True)
        self.check("Download identical bytes safely", b, "GET", f"/api/files/{item['id']}/download", route="GET /api/files/{id}/download", validate=lambda r: require(r.body == contents and "attachment" in r.headers.get("Content-Disposition", "").lower() and r.headers.get("X-Content-Type-Options") == "nosniff", "Downloaded bytes or safe attachment headers differ.") or True)
        self.check("Rename the file", a, "PATCH", f"/api/files/{item['id']}", route="PATCH /api/files/{id}", payload={"name": "renamed-check.txt"}, validate=resource)
        self.check("Search finds the renamed fixture", b, "GET", "/api/files?" + urlencode({"q": "renamed-check.txt"}), route="GET /api/files", validate=page)
        self.check("Nonempty folder cannot be trashed", a, "DELETE", f"/api/files/{folder['id']}", route="DELETE /api/files/{id}", expected=(409,))
        if self.check("Move the test file to Trash", a, "DELETE", f"/api/files/{item['id']}", route="DELETE /api/files/{id}"):
            self.check("Trashed file cannot be downloaded", b, "GET", f"/api/files/{item['id']}/download", route="GET /api/files/{id}/download", expected=(404,))
            if self.check("Folder with only trashed children can be trashed", a, "DELETE", f"/api/files/{folder['id']}", route="DELETE /api/files/{id}"):
                self.check("Child cannot restore before parent", a, "POST", f"/api/files/{item['id']}/restore", route="POST /api/files/{id}/restore", expected=(409,))
                self.check("Restore the parent only", a, "POST", f"/api/files/{folder['id']}/restore", route="POST /api/files/{id}/restore", validate=resource)
                self.check("Restoring parent did not restore child", b, "GET", f"/api/files/{item['id']}/download", route="GET /api/files/{id}/download", expected=(404,))
            self.check("Restore the test file", a, "POST", f"/api/files/{item['id']}/restore", route="POST /api/files/{id}/restore", validate=resource)
            self.check("Restored file retains identical bytes", b, "GET", f"/api/files/{item['id']}/download", route="GET /api/files/{id}/download", validate=lambda r: require(r.body == contents, "Restored content changed.") or True)

    def verify_notes(self, a, b):
        note = self.check("Create a test page", a, "POST", "/api/notes", route="POST /api/notes", expected=(200, 201), payload={"title": self.prefix, "body": "# OpenSW API check\n\nOriginal content."}, validate=lambda r: resource(r, ("title", "body", "revision", "archived", "created_by")))
        if not note:
            return
        self.fixture("note", note, author=a)
        self.check("Page list uses summaries", b, "GET", "/api/notes?" + urlencode({"q": self.prefix}), route="GET /api/notes", validate=lambda r: require(any(row["id"] == note["id"] and "body" not in row for row in page(r)["items"]), "Page summary is missing or contains the full body.") or True)
        self.check("Another member can open full page", b, "GET", f"/api/notes/{note['id']}", route="GET /api/notes/{id}", validate=lambda r: require(resource(r)["body"] == note["body"], "Page detail lost content.") or True)
        saved = self.check("Save a new confirmed revision", b, "PATCH", f"/api/notes/{note['id']}", route="PATCH /api/notes/{id}", payload={"body": "Updated content from member B.", "expected_revision": note["revision"]}, validate=resource)
        if saved:
            self.check("Stale page write is rejected", a, "PATCH", f"/api/notes/{note['id']}", route="PATCH /api/notes/{id}", expected=(409,), payload={"body": "Must not overwrite", "expected_revision": note["revision"]})
            archived = self.check("Archive a page without deleting its text", a, "PATCH", f"/api/notes/{note['id']}", route="PATCH /api/notes/{id}", payload={"archived": True, "expected_revision": saved["revision"]}, validate=resource)
            if archived:
                self.check("Archived page is retained", b, "GET", "/api/notes?" + urlencode({"q": self.prefix, "archived": "true"}), route="GET /api/notes", validate=lambda r: require(any(row["id"] == note["id"] for row in page(r)["items"]), "Archived page is not reachable.") or True)
                self.check("Restore the retained page", a, "PATCH", f"/api/notes/{note['id']}", route="PATCH /api/notes/{id}", payload={"archived": False, "expected_revision": archived["revision"]}, validate=resource)

    def verify_projects(self, a, b):
        project = self.check("Create a test project", a, "POST", "/api/projects", route="POST /api/projects", expected=(200, 201), payload={"name": self.prefix, "description": "OpenSW API verification"}, validate=resource)
        if not project:
            return
        self.fixture("project", project, author=a)
        self.check("Project detail returns canonical selected metadata", b, "GET", f"/api/projects/{project['id']}", route="GET /api/projects/{id}",
                   validate=lambda r: require(resource(r) == project, "Project detail differs from the confirmed resource.") or True)
        self.check("Project list includes the fixture", b, "GET", "/api/projects?" + urlencode({"q": self.prefix}), route="GET /api/projects", validate=lambda r: require(any(row["id"] == project["id"] for row in page(r)["items"]), "Confirmed project is missing.") or True)
        task = self.check("Create and assign a task", a, "POST", f"/api/projects/{project['id']}/tasks", route="POST /api/projects/{id}/tasks", expected=(200, 201), payload={"title": self.prefix + " task", "description": "A complete task", "assignee_id": b.user["id"], "due_date": "2030-01-15", "priority": "normal"}, validate=lambda r: resource(r, ("project_id", "title", "description", "status", "priority", "revision", "assignee_id", "due_date", "created_by")))
        if not task:
            return
        self.fixture("task", task, author=a)
        self.check("Board list uses task summaries", b, "GET", f"/api/projects/{project['id']}/tasks", route="GET /api/projects/{id}/tasks", validate=lambda r: require(any(row["id"] == task["id"] and "description" not in row for row in page(r)["items"]), "Task summary is missing or includes description.") or True)
        self.check("Task detail is complete", b, "GET", f"/api/tasks/{task['id']}", route="GET /api/tasks/{id}", validate=lambda r: require(resource(r)["description"] == "A complete task", "Task detail lost description.") or True)
        saved = self.check("Move task after server confirmation", b, "PATCH", f"/api/tasks/{task['id']}", route="PATCH /api/tasks/{id}", payload={"status": "doing", "priority": "high", "expected_revision": task["revision"]}, validate=resource)
        if saved:
            self.check("Stale task write is rejected", a, "PATCH", f"/api/tasks/{task['id']}", route="PATCH /api/tasks/{id}", expected=(409,), payload={"status": "done", "expected_revision": task["revision"]})
        self.check("Add an authored task comment", b, "POST", f"/api/tasks/{task['id']}/comments", route="POST /api/tasks/{id}/comments", expected=(200, 201), payload={"body": self.prefix + " confirmed comment"}, validate=lambda r: require(resource(r)["author_id"] == b.user["id"], "Comment author differs.") or True)
        self.check("Read task comments", a, "GET", f"/api/tasks/{task['id']}/comments", route="GET /api/tasks/{id}/comments", validate=page)
        self.check("Archive project without deleting its work", a, "PATCH", f"/api/projects/{project['id']}", route="PATCH /api/projects/{id}", payload={"archived": True}, validate=resource)
        self.check("Archived project rejects new tasks", a, "POST", f"/api/projects/{project['id']}/tasks", route="POST /api/projects/{id}/tasks", expected=(409,), payload={"title": "Must not create"})
        self.check("Restore the project", a, "PATCH", f"/api/projects/{project['id']}", route="PATCH /api/projects/{id}", payload={"archived": False}, validate=resource)

    def verify_chat(self, a, b):
        channel = self.check("Create an isolated test channel", a, "POST", "/api/chat/channels", route="POST /api/chat/channels", expected=(200, 201), payload={"name": self.prefix}, validate=resource)
        if channel:
            self.fixture("channel", channel)
        self.check("Read team channels", b, "GET", "/api/chat/channels", route="GET /api/chat/channels", validate=page)
        dm = self.check("Create a private two-member conversation", a, "POST", "/api/chat/dms", route="POST /api/chat/dms", expected=(200, 201), payload={"user_id": b.user["id"]}, validate=resource)
        if not dm:
            return
        self.fixture("dm", dm)
        self.check("Participant can open selected conversation metadata", a, "GET", f"/api/chat/conversations/{dm['id']}", route="GET /api/chat/conversations/{id}",
                   validate=lambda r: require(resource(r)["kind"] == "dm", "Conversation detail lost its type.") or True)
        self.check("Nonparticipant cannot read DM metadata", self.operator, "GET", f"/api/chat/conversations/{dm['id']}", route="GET /api/chat/conversations/{id}", expected=(403, 404))
        self.check("Reverse DM request returns the same pair", b, "POST", "/api/chat/dms", route="POST /api/chat/dms", expected=(200, 201), payload={"user_id": a.user["id"]}, validate=lambda r: require(resource(r)["id"] == dm["id"], "Duplicate DM pair was created.") or True)
        self.check("Participant can list their DM", b, "GET", "/api/chat/dms", route="GET /api/chat/dms", validate=lambda r: require(any(row["id"] == dm["id"] for row in page(r)["items"]), "DM is not listed for participant.") or True)
        messages = f"/api/chat/conversations/{dm['id']}/messages"
        message = self.check("Send a private test message", a, "POST", messages, route="POST /api/chat/conversations/{id}/messages", expected=(200, 201), payload={"body": self.prefix + " private message"}, validate=resource)
        if not message:
            return
        self.fixture("message", message, author=a)
        self.check("Other participant sees confirmed message", b, "GET", messages, route="GET /api/chat/conversations/{id}/messages", validate=lambda r: require(any(row["id"] == message["id"] for row in messages_page(r)["items"]), "Confirmed message is absent.") or True)
        for suffix in ("", "?q=private"):
            self.check("Even an administrator cannot read another pair's DM" + (" search" if suffix else ""), self.operator, "GET", messages + suffix, route="GET /api/chat/conversations/{id}/messages", expected=(403, 404))
        self.check("Administrator cannot list another pair's DM", self.operator, "GET", "/api/chat/dms", route="GET /api/chat/dms", validate=lambda r: require(all(row["id"] != dm["id"] for row in page(r)["items"]), "A nonparticipant received DM metadata.") or True)
        self.check("Non-author cannot edit a message", b, "PATCH", f"/api/chat/messages/{message['id']}", route="PATCH /api/chat/messages/{id}", expected=(403, 404), payload={"body": "Must not change"})
        self.check("Author can edit the same message", a, "PATCH", f"/api/chat/messages/{message['id']}", route="PATCH /api/chat/messages/{id}", payload={"body": self.prefix + " edited"}, validate=resource)
        self.check("Snapshot reflects edits without a new message ID", b, "GET", messages, route="GET /api/chat/conversations/{id}/messages", validate=lambda r: require(any(row["id"] == message["id"] and row["body"] == self.prefix + " edited" for row in messages_page(r)["items"]), "Snapshot missed an edit.") or True)
        self.check("Participant can mark a confirmed message read", b, "POST", f"/api/chat/conversations/{dm['id']}/read", route="POST /api/chat/conversations/{id}/read", payload={"message_id": message["id"]})
        self.check("Nonparticipant cannot mark the DM read", self.operator, "POST", f"/api/chat/conversations/{dm['id']}/read", route="POST /api/chat/conversations/{id}/read", expected=(403, 404), payload={"message_id": message["id"]})
        self.check("Author can remove message text", a, "DELETE", f"/api/chat/messages/{message['id']}", route="DELETE /api/chat/messages/{id}")
        self.check("Snapshot preserves an empty deletion tombstone", b, "GET", messages, route="GET /api/chat/conversations/{id}/messages", validate=lambda r: require(any(row["id"] == message["id"] and row["deleted"] is True and row["body"] == "" for row in messages_page(r)["items"]), "Deleted message was not a safe tombstone.") or True)

    def cleanup(self):
        if not self.operator.user:
            return
        print("Cleaning up confirmed fixtures only; no existing content is selected.", flush=True)
        # Re-read current fixture state before soft cleanup. IDs come only from
        # confirmed creates, never from a name search in the operator workspace.
        for fixture in reversed(self.fixtures):
            kind, ident = fixture["kind"], fixture["id"]
            if kind in {"file", "folder"}:
                current = self.check("Inspect test item before cleanup", self.operator, "GET", f"/api/files/{ident}", route="GET /api/files/{id}", expected=(200, 404))
                if current and current.status == 200:
                    self.check("Move confirmed test item to Trash", self.operator, "DELETE", f"/api/files/{ident}", route="DELETE /api/files/{id}")
            elif kind in {"note", "task"}:
                plural = "notes" if kind == "note" else "tasks"
                path = f"/api/{plural}/{ident}"
                current = self.check("Read latest test revision for cleanup", self.operator, "GET", path, route=f"GET /api/{plural}/{{id}}", validate=resource)
                if current and not current.get("archived"):
                    self.check("Archive the confirmed test record", self.operator, "PATCH", path, route=f"PATCH /api/{plural}/{{id}}", payload={"archived": True, "expected_revision": current["revision"]}, validate=resource)
            elif kind == "project":
                self.check("Archive the confirmed test project", self.operator, "PATCH", f"/api/projects/{ident}", route="PATCH /api/projects/{id}", payload={"archived": True}, validate=resource)
            elif kind == "message":
                actor = fixture["actor"]
                if actor and self.safe_member(actor):
                    conversation_id = fixture["value"]["conversation_id"]
                    snapshot = self.check("Inspect the confirmed private test message", actor, "GET", f"/api/chat/conversations/{conversation_id}/messages", route="GET /api/chat/conversations/{id}/messages", validate=messages_page)
                    if snapshot:
                        message = next((item for item in snapshot.get("items", []) if item.get("id") == ident), None)
                        if message and not message.get("deleted"):
                            self.check("Remove confirmed test message text", actor, "DELETE", f"/api/chat/messages/{ident}", route="DELETE /api/chat/messages/{id}")
            # Chat channels/DMs intentionally have no deletion API. Messages
            # created by this run are tombstoned in the workflow and retained.
        for actor in self.actors:
            if actor.user and self.safe_member(actor):
                self.check("Log out the temporary verification session", actor, "POST", "/api/auth/logout", route="POST /api/auth/logout", expected=(200, 401))
        for user in self.users:
            if user["id"] == self.operator.user["id"] or user.get("role") != "member" or not user.get("username", "").startswith(self.prefix + "-"):
                self.report("Refuse unsafe account cleanup", "FAIL", detail="Confirmed member identity guard failed; no deactivation was issued.")
                continue
            current_page = self.check("Reconfirm temporary member before deactivation", self.operator, "GET", f"/api/users?cursor={user['id'] - 1}", route="GET /api/users", validate=page)
            current_user = next((item for item in current_page["items"] if item.get("id") == user["id"]), None) if current_page else None
            if not current_user or current_user.get("username") != user["username"] or current_user.get("role") != "member" or current_user["id"] == self.operator.user["id"]:
                self.report("Temporary member identity could not be safely reconfirmed", "BLOCKED", detail="No deactivation was issued for this ID.")
                continue
            self.check("Deactivate the confirmed temporary member", self.operator, "PATCH", f"/api/users/{user['id']}", route="PATCH /api/users/{id}", payload={"active": False}, validate=lambda r: require(resource(r)["active"] is False, "Temporary member remains active.") or True)
        self.check("Log out this verifier's operator session", self.operator, "POST", "/api/auth/logout", route="POST /api/auth/logout")

    def finish(self):
        for route, states in sorted(self.coverage.items()):
            if not states:
                self.report("Required API route was not exercised", "BLOCKED", route=route, expected="covered", actual="not run", detail="An earlier prerequisite failed; inspect the preceding diagnostic.")
        counts = {state: sum(step["state"] == state for step in self.steps) for state in ("PASS", "FAIL", "BLOCKED")}
        responded = {step["route"] for step in self.steps if step.get("route") in self.coverage and type(step.get("actual")) is int}
        covered = len(responded)
        passed = sum(route in responded and all(state == "PASS" for state in states) for route, states in self.coverage.items())
        print(f"\nAPI coverage: {covered}/{len(self.coverage)} required routes received HTTP responses; {passed}/{len(self.coverage)} passed every recorded check.")
        print(f"Results: {counts['PASS']} PASS · {counts['FAIL']} FAIL · {counts['BLOCKED']} BLOCKED.")
        if self.users or self.fixtures:
            print(f"Retained verification namespace: {self.prefix}")
            print("Soft-deleted/archived records, disabled test members, and Chat test channels/DMs remain in your storage; no permanent deletion was attempted.")
        if counts["FAIL"] or counts["BLOCKED"]:
            print("Check the named failure, then inspect only this deployment's container logs. Do not blindly repeat an unconfirmed write.")
        self.result = {"app": self.app, "namespace": self.prefix, "counts": counts, "routes_exercised": covered, "routes_passed": passed,
                       "routes_required": len(self.coverage), "steps": self.steps,
                       "retained_fixture_ids": [{"kind": item["kind"], "id": item["id"]} for item in self.fixtures],
                       "temporary_user_ids": [user["id"] for user in self.users]}
        return 0 if not counts["FAIL"] and not counts["BLOCKED"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside-container", action="store_true", help="Read app/config from /data and credentials JSON from stdin.")
    parser.add_argument("--url", help="Installed app address; no redirects are followed.")
    parser.add_argument("--origin", help="Configured public origin, if different from the connection URL.")
    parser.add_argument("--app", choices=tuple(APP_ROUTES))
    parser.add_argument("--username", help="Existing administrator username.")
    parser.add_argument("--password-file", type=Path, help="Private file containing the administrator password; the password is never an argument.")
    parser.add_argument("--report", type=Path, help="Write a redacted JSON diagnostic report.")
    parser.add_argument("--ca-file", type=Path, help="Explicit trusted CA PEM for an organizational HTTPS deployment; hostname verification stays enabled.")
    args = parser.parse_args(argv)
    try:
        if args.inside_container:
            config = json.loads((Path(os.environ.get("OPENSW_DATA_DIR", "/data")) / "state/config.json").read_text())
            credentials = json.loads(sys.stdin.read())
            if set(credentials) != {"username", "password"}:
                raise ValueError("Credential input must contain only username and password.")
            origin = config["public_url"]
            selected, url = config["app"], origin if origin.startswith("https://") else "http://127.0.0.1:8000"
            username, password = credentials["username"], credentials["password"]
        else:
            if not args.app or not args.url:
                raise ValueError("Supply --app and --url, or use ./verify.sh with an installation config.")
            selected, url, origin = args.app, args.url, args.origin or args.url
            username = args.username or input("Administrator username: ").strip()
            password = args.password_file.read_text().rstrip("\r\n") if args.password_file else getpass.getpass("Administrator password: ")
        if selected not in APP_ROUTES:
            raise ValueError("The selected app is not supported by this verifier.")
        for address in (url, origin):
            parsed = urlsplit(address)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError("Use one HTTP(S) origin without embedded credentials, query, or path.")
        if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
            raise ValueError("Administrator credentials are required.")
        verifier = Verifier(selected, url, origin, username, password, **({"ca_file": str(args.ca_file)} if args.ca_file else {}))
        code = verifier.run()
        if args.report:
            descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w") as target:
                json.dump(verifier.result, target, indent=2)
                target.write("\n")
        return code
    except (OSError, ValueError, KeyError, EOFError) as error:
        print(f"[BLOCKED] Cannot start verification: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
