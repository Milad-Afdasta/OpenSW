"""Black-box owner invariants for all four independent OpenSW apps."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from opensw.main import create_app
from conftest import (
    ADMIN_PASSWORD, APP_IDS, MEMBER_PASSWORD, SignedIn, all_items, member,
)


ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "shared/catalog.json").read_text())
MODULE_ROOTS = {
    "files": "/api/files", "projects": "/api/projects",
    "notes": "/api/notes", "chat": "/api/chat/channels",
}
USER_FIELDS = {"id", "username", "display_name", "role", "active"}
FILE_FIELDS = {
    "id", "name", "kind", "parent_id", "size", "created_by",
    "created_at", "updated_at", "deleted_at",
}
NOTE_FIELDS = {
    "id", "title", "body", "archived", "revision", "created_by",
    "created_at", "updated_at",
}
TASK_FIELDS = {
    "id", "project_id", "title", "description", "status", "priority",
    "assignee_id", "due_date", "archived", "revision", "created_by",
    "created_at", "updated_at",
}


def created(response):
    assert response.status_code in (200, 201), response.text
    return response.json()


@pytest.mark.parametrize("app_id", APP_IDS)
def test_app_identity_auth_and_only_selected_routes(app_factory, app_id):
    _, client, data = app_factory(app_id)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok", "app": app_id, "version": CATALOG["version"], "schema_version": 1,
    }
    assert client.get("/api/me").status_code == 401
    user = SignedIn(client)
    assert set(user.user) == USER_FIELDS
    assert user.session["app"] == app_id
    assert "apps" not in user.session
    assert user.session["team_name"] == "OpenSW test team"
    assert user.get(MODULE_ROOTS[app_id]).status_code == 200
    for other, route in MODULE_ROOTS.items():
        if other != app_id:
            assert user.get(route).status_code == 404
    cookie = next(c for c in client.cookies.jar if c.name.endswith("_session"))
    assert cookie.name == f"opensw_{app_id}_test_session"
    assert not cookie.domain_specified
    assert not (data / "state/bootstrap.json").exists()


def test_same_ids_and_credentials_do_not_share_sessions_between_apps(app_factory):
    _, files, _ = app_factory("files")
    _, notes, _ = app_factory("notes")
    file_user = SignedIn(files)
    notes.cookies.update(files.cookies)
    assert notes.get("/api/me").status_code == 401
    note_user = SignedIn(notes)
    assert file_user.user["id"] == note_user.user["id"]
    assert file_user.csrf != note_user.csrf
    response = notes.post("/api/notes", json={"title": "Cross-app attempt"}, headers={
        "Origin": "http://testserver", "X-CSRF-Token": file_user.csrf,
    })
    assert response.status_code == 403
    assert all_items(note_user, "/api/notes") == []


def test_https_cookie_is_host_only_http_only_secure_and_strict(app_factory):
    _, client, _ = app_factory("notes", config_overrides={"public_url": "https://notes.example.test"})
    response = client.post("/api/auth/login", json={
        "username": "admin", "password": ADMIN_PASSWORD,
    }, headers={"Origin": "https://notes.example.test"})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    assert "domain=" not in cookie


@pytest.mark.parametrize("app_id", APP_IDS)
def test_disabling_an_author_preserves_complete_visible_content(app_factory, app_id):
    app, client, _ = app_factory(app_id)
    admin = SignedIn(client)
    author = member(admin, app, "retained-author")
    if app_id == "files":
        row = created(author.post("/api/files/upload", files={"file": ("authored.txt", b"retained")}))
        get_path = f"/api/files/{row['id']}"
        author_field = "created_by"
    elif app_id == "notes":
        row = created(author.post("/api/notes", json={"title": "Retained page", "body": "Written before disable"}))
        get_path = f"/api/notes/{row['id']}"
        author_field = "created_by"
    elif app_id == "projects":
        project = created(admin.post("/api/projects", json={"name": "Retained project"}))
        row = created(author.post(f"/api/projects/{project['id']}/tasks", json={"title": "Retained task"}))
        get_path = f"/api/tasks/{row['id']}"
        author_field = "created_by"
    else:
        channel = created(admin.post("/api/chat/channels", json={"name": "retained"}))
        get_path = f"/api/chat/conversations/{channel['id']}/messages"
        row = created(author.post(get_path, json={"body": "Authorship remains"}))
        author_field = "author_id"
    assert admin.patch(f"/api/users/{author.user['id']}", json={"active": False}).status_code == 200
    assert author.get("/api/me").status_code == 401
    retained_response = admin.get(get_path)
    assert retained_response.status_code == 200
    retained = retained_response.json()
    if app_id == "chat":
        retained = next(m for m in retained["items"] if m["id"] == row["id"])
    assert retained[author_field] == author.user["id"]
    users = all_items(admin, "/api/users")
    assert next(u for u in users if u["id"] == author.user["id"])["active"] is False


@pytest.mark.parametrize("app_id", APP_IDS)
def test_origin_csrf_password_change_and_disabled_sessions(app_factory, app_id):
    app, client, _ = app_factory(app_id)
    bad_origin = client.post("/api/auth/login", json={
        "username": "admin", "password": ADMIN_PASSWORD,
    }, headers={"Origin": "https://another-app.invalid"})
    assert bad_origin.status_code == 403
    admin = SignedIn(client)
    no_csrf = client.post("/api/users", json={
        "username": "outsider", "display_name": "Outsider", "password": MEMBER_PASSWORD,
    }, headers={"Origin": "http://testserver"})
    assert no_csrf.status_code == 403
    teammate = member(admin, app, "teammate")
    assert teammate.post("/api/users", json={
        "username": "another", "display_name": "Another", "password": MEMBER_PASSWORD,
    }).status_code == 403
    assert admin.patch(f"/api/users/{admin.user['id']}", json={"active": False}).status_code in (400, 409)
    assert admin.patch(f"/api/users/{teammate.user['id']}", json={"active": False}).status_code == 200
    assert teammate.get("/api/me").status_code == 401
    retained = all_items(admin, "/api/users")
    assert any(row["id"] == teammate.user["id"] and row["active"] is False for row in retained)
    assert admin.post("/api/auth/password", json={
        "current_password": ADMIN_PASSWORD, "new_password": "New-OpenSW-password-9!",
    }).status_code == 200
    assert admin.get("/api/me").status_code == 401
    old_login = client.post("/api/auth/login", json={
        "username": "admin", "password": ADMIN_PASSWORD,
    }, headers={"Origin": "http://testserver"})
    assert old_login.status_code == 401
    SignedIn(client, password="New-OpenSW-password-9!")


def test_files_shared_bytes_duplicate_trash_restore_and_safe_download(app_factory):
    app, client, _ = app_factory("files")
    admin = SignedIn(client)
    teammate = member(admin, app, "reader")
    folder = created(admin.post("/api/files/folders", json={"name": "Research", "parent_id": None}))
    payload = b"OpenSW original bytes\x00\xff\n"
    file = created(admin.post("/api/files/upload", files={
        "file": ("report.html", payload, "text/html"),
    }, data={"parent_id": str(folder["id"])}))
    assert set(file) == FILE_FIELDS
    downloaded = teammate.get(f"/api/files/{file['id']}/download")
    assert downloaded.content == payload
    assert "attachment" in downloaded.headers["content-disposition"].lower()
    assert downloaded.headers.get("x-content-type-options") == "nosniff"
    anonymous = TestClient(app, base_url="http://testserver")
    assert anonymous.get(f"/api/files/{file['id']}/download").status_code == 401
    assert admin.post("/api/files/upload", files={
        "file": ("report.html", b"must not overwrite", "text/plain"),
    }, data={"parent_id": str(folder["id"])}).status_code == 409
    assert teammate.get(f"/api/files/{file['id']}/download").content == payload
    empty_folder = created(admin.post("/api/files/folders", json={"name": "Empty", "parent_id": None}))
    assert admin.delete(f"/api/files/{empty_folder['id']}").status_code == 200
    assert admin.post(f"/api/files/{empty_folder['id']}/restore").status_code == 200
    assert admin.delete(f"/api/files/{folder['id']}").status_code == 409
    assert admin.delete(f"/api/files/{file['id']}").status_code == 200
    assert teammate.get(f"/api/files/{file['id']}/download").status_code == 404
    assert admin.delete(f"/api/files/{folder['id']}").status_code == 200
    assert admin.post(f"/api/files/{file['id']}/restore").status_code == 409
    assert admin.post(f"/api/files/{folder['id']}/restore").status_code == 200
    assert teammate.get(f"/api/files/{file['id']}/download").status_code == 404
    replacement = created(admin.post("/api/files/upload", files={
        "file": ("report.html", b"replacement", "text/plain"),
    }, data={"parent_id": str(folder["id"])}))
    assert admin.post(f"/api/files/{file['id']}/restore").status_code == 409
    assert admin.delete(f"/api/files/{replacement['id']}").status_code == 200
    assert admin.post(f"/api/files/{file['id']}/restore").status_code == 200
    assert teammate.get(f"/api/files/{file['id']}/download").content == payload


def test_files_reject_stale_destination_size_and_path_names(app_factory):
    _, client, data = app_factory("files")
    admin = SignedIn(client)
    folder = created(admin.post("/api/files/folders", json={"name": "Gone", "parent_id": None}))
    assert admin.delete(f"/api/files/{folder['id']}").status_code == 200
    stale = admin.post("/api/files/upload", files={"file": ("late.txt", b"late")}, data={
        "parent_id": str(folder["id"]),
    })
    assert stale.status_code in (404, 409, 422)
    oversized = admin.post("/api/files/upload", files={
        "file": ("large.bin", b"x" * (1024 * 1024 + 1)),
    })
    assert oversized.status_code == 413
    for name in ("../escape.txt", "..", "bad/name.txt", "bad\\name.txt"):
        rejected = admin.post("/api/files/upload", files={"file": (name, b"unsafe")})
        assert rejected.status_code in (400, 422), (name, rejected.text)
    assert all_items(admin, "/api/files") == []
    assert not (data.parent / "escape.txt").exists()


def test_notes_complete_detail_summaries_conflicts_and_archive(app_factory):
    app, client, _ = app_factory("notes")
    admin = SignedIn(client)
    teammate = member(admin, app, "editor")
    note = created(admin.post("/api/notes", json={
        "title": "Launch guide", "body": "# Start here\n\nOriginal text",
    }))
    assert set(note) == NOTE_FIELDS
    listing = all_items(teammate, "/api/notes")
    assert listing[0]["id"] == note["id"]
    assert "body" not in listing[0]
    saved = created(teammate.patch(f"/api/notes/{note['id']}", json={
        "body": "Updated by teammate", "expected_revision": note["revision"],
    }))
    assert saved["revision"] == note["revision"] + 1
    stale = admin.patch(f"/api/notes/{note['id']}", json={
        "body": "Must not overwrite", "expected_revision": note["revision"],
    })
    assert stale.status_code == 409
    assert admin.get(f"/api/notes/{note['id']}").json()["body"] == "Updated by teammate"
    archived = created(admin.patch(f"/api/notes/{note['id']}", json={
        "archived": True, "expected_revision": saved["revision"],
    }))
    assert all_items(admin, "/api/notes") == []
    assert all_items(admin, "/api/notes?archived=true")[0]["id"] == note["id"]
    restored = created(admin.patch(f"/api/notes/{note['id']}", json={
        "archived": False, "expected_revision": archived["revision"],
    }))
    assert restored["body"] == "Updated by teammate"


@pytest.mark.parametrize("app_id", ("notes", "projects"))
def test_concurrent_editors_cannot_both_commit_one_revision(app_factory, app_id):
    app, client, _ = app_factory(app_id)
    admin = SignedIn(client)
    teammate = member(admin, app, "second-editor")
    if app_id == "notes":
        resource = created(admin.post("/api/notes", json={"title": "One revision"}))
        path = f"/api/notes/{resource['id']}"
    else:
        project = created(admin.post("/api/projects", json={"name": "One revision"}))
        resource = created(admin.post(f"/api/projects/{project['id']}/tasks", json={"title": "One revision"}))
        path = f"/api/tasks/{resource['id']}"
    barrier = threading.Barrier(2)

    def write(actor, title):
        barrier.wait(timeout=5)
        return actor.patch(path, json={"title": title, "expected_revision": resource["revision"]})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(write, admin, "First editor")
        second = pool.submit(write, teammate, "Second editor")
        responses = [first.result(timeout=15), second.result(timeout=15)]
    assert sorted(r.status_code for r in responses) == [200, 409]
    final = admin.get(path).json()
    assert final["revision"] == resource["revision"] + 1
    assert final["title"] in ("First editor", "Second editor")


def test_projects_catalog_revision_assignees_comments_and_archive(app_factory):
    app, client, _ = app_factory("projects")
    admin = SignedIn(client)
    teammate = member(admin, app, "worker")
    project = created(admin.post("/api/projects", json={"name": "Launch", "description": "Ship it"}))
    task = created(admin.post(f"/api/projects/{project['id']}/tasks", json={
        "title": "Prepare release", "description": "Keep it small",
        "assignee_id": teammate.user["id"], "due_date": "2026-11-07",
    }))
    assert set(task) == TASK_FIELDS
    listing = all_items(admin, f"/api/projects/{project['id']}/tasks")
    assert "description" not in listing[0]
    assert admin.get(f"/api/tasks/{task['id']}").json()["description"] == "Keep it small"
    for status in CATALOG["task_statuses"]:
        task = created(admin.patch(f"/api/tasks/{task['id']}", json={
            "status": status["id"], "expected_revision": task["revision"],
        }))
        assert task["status"] == status["id"]
    for priority in CATALOG["task_priorities"]:
        task = created(admin.patch(f"/api/tasks/{task['id']}", json={
            "priority": priority["id"], "expected_revision": task["revision"],
        }))
        assert task["priority"] == priority["id"]
    assert admin.patch(f"/api/tasks/{task['id']}", json={
        "title": "Stale", "expected_revision": 1,
    }).status_code == 409
    for invalid in ({"status": "legacy"}, {"priority": "urgent"}, {"assignee_id": 999999}, {"due_date": "2026-02-30"}):
        response = admin.patch(f"/api/tasks/{task['id']}", json={
            **invalid, "expected_revision": task["revision"],
        })
        assert response.status_code == 422, response.text
    comment = created(teammate.post(f"/api/tasks/{task['id']}/comments", json={"body": "Ready to review"}))
    assert comment["author_id"] == teammate.user["id"]
    assert admin.patch(f"/api/users/{teammate.user['id']}", json={"active": False}).status_code == 200
    task = created(admin.patch(f"/api/tasks/{task['id']}", json={
        "title": "Retained authorship", "assignee_id": teammate.user["id"],
        "expected_revision": task["revision"],
    }))
    assert task["assignee_id"] == teammate.user["id"]
    assert all_items(admin, f"/api/tasks/{task['id']}/comments")[0]["author_id"] == teammate.user["id"]
    task = created(admin.patch(f"/api/tasks/{task['id']}", json={
        "archived": True, "expected_revision": task["revision"],
    }))
    assert all_items(admin, f"/api/projects/{project['id']}/tasks") == []
    assert all_items(admin, f"/api/projects/{project['id']}/tasks?archived=true")[0]["id"] == task["id"]
    task = created(admin.patch(f"/api/tasks/{task['id']}", json={
        "archived": False, "expected_revision": task["revision"],
    }))
    assert admin.patch(f"/api/projects/{project['id']}", json={"archived": True}).status_code == 200
    assert admin.post(f"/api/projects/{project['id']}/tasks", json={"title": "Blocked"}).status_code == 409
    assert admin.post(f"/api/tasks/{task['id']}/comments", json={"body": "Blocked"}).status_code == 409
    assert all_items(admin, "/api/projects") == []
    assert all_items(admin, "/api/projects?archived=true")[0]["id"] == project["id"]
    assert admin.patch(f"/api/projects/{project['id']}", json={"archived": False}).status_code == 200


def test_chat_dm_privacy_including_admin_and_message_snapshot_mutations(app_factory):
    app, client, _ = app_factory("chat")
    admin = SignedIn(client)
    alice = member(admin, app, "alice")
    bob = member(admin, app, "bob")
    stranger = member(admin, app, "stranger")
    conversation = created(alice.post("/api/chat/dms", json={"user_id": bob.user["id"]}))
    reverse = created(bob.post("/api/chat/dms", json={"user_id": alice.user["id"]}))
    assert reverse["id"] == conversation["id"]
    assert alice.post("/api/chat/dms", json={"user_id": alice.user["id"]}).status_code == 422
    path = f"/api/chat/conversations/{conversation['id']}/messages"
    message = created(alice.post(path, json={"body": "Private release discussion"}))
    detail_path = f"/api/chat/conversations/{conversation['id']}"
    alice_detail = alice.get(detail_path)
    assert alice_detail.status_code == 200
    assert alice_detail.json() == conversation
    assert bob.get(detail_path).json()["peer"]["id"] == alice.user["id"]
    for outsider in (admin, stranger):
        assert outsider.get(detail_path).status_code == 404
        assert outsider.get(path).status_code in (403, 404)
        assert outsider.get(f"{path}?q=release").status_code in (403, 404)
        assert outsider.post(path, json={"body": "Forbidden"}).status_code in (403, 404)
        assert outsider.post(f"/api/chat/conversations/{conversation['id']}/read", json={
            "message_id": message["id"],
        }).status_code in (403, 404)
        assert outsider.patch(f"/api/chat/messages/{message['id']}", json={"body": "Forbidden"}).status_code in (403, 404)
        assert outsider.delete(f"/api/chat/messages/{message['id']}").status_code in (403, 404)
        assert all(row["id"] != conversation["id"] for row in all_items(outsider, "/api/chat/dms"))
    assert bob.patch(f"/api/chat/messages/{message['id']}", json={"body": "Not my message"}).status_code in (403, 404)
    edited = created(alice.patch(f"/api/chat/messages/{message['id']}", json={"body": "Edited private text"}))
    assert edited["id"] == message["id"]
    assert bob.get(path).json()["items"][0]["body"] == "Edited private text"
    assert alice.delete(f"/api/chat/messages/{message['id']}").status_code == 200
    tombstone = bob.get(path).json()["items"][0]
    assert tombstone["id"] == message["id"]
    assert tombstone["deleted"] is True and tombstone["body"] == ""
    assert bob.get(f"{path}?q=Edited").json()["items"] == []


def test_project_detail_is_complete_authenticated_and_independent_of_sidebar_page(app_factory):
    app, client, _ = app_factory("projects")
    admin = SignedIn(client)
    assert TestClient(app).get("/api/projects/1").status_code == 401
    made = [created(admin.post("/api/projects", json={"name": f"Project {index:03d}", "description": f"Complete metadata {index}"})) for index in range(101)]
    selected = made[-1]
    assert selected["id"] not in {row["id"] for row in admin.get("/api/projects").json()["items"]}
    detail = admin.get(f"/api/projects/{selected['id']}")
    assert detail.status_code == 200 and detail.json() == selected
    archived = created(admin.patch(f"/api/projects/{selected['id']}", json={"archived": True}))
    assert admin.get(f"/api/projects/{selected['id']}").json() == archived
    assert admin.get("/api/projects/999999").status_code == 404
    assert admin.get(f"/api/projects/{2**80}").status_code == 422


def test_conversation_detail_uses_complete_participant_metadata_and_current_read_owner(app_factory):
    app, client, _ = app_factory("chat")
    admin = SignedIn(client)
    alice = member(admin, app, "alice")
    bob = member(admin, app, "bob")
    assert TestClient(app).get("/api/chat/conversations/1").status_code == 401
    channels = [created(admin.post("/api/chat/channels", json={"name": f"detail-{index:03d}"})) for index in range(101)]
    selected = channels[-1]
    assert selected["id"] not in {row["id"] for row in alice.get("/api/chat/channels").json()["items"]}
    assert alice.get(f"/api/chat/conversations/{selected['id']}").json() == selected
    direct = created(alice.post("/api/chat/dms", json={"user_id": bob.user["id"]}))
    path = f"/api/chat/conversations/{direct['id']}"
    message = created(alice.post(path + "/messages", json={"body": "Unread detail"}))
    detail = bob.get(path).json()
    assert set(detail) == set(direct)
    assert detail["peer"] == alice.user and detail["name"] == alice.user["display_name"] and detail["unread_count"] == 1
    assert bob.post(path + "/read", json={"message_id": message["id"]}).status_code == 200
    assert bob.get(path).json()["unread_count"] == 0
    assert admin.patch(f"/api/users/{bob.user['id']}", json={"active": False}).status_code == 200
    retained = alice.get(path).json()
    assert retained["peer"]["active"] is False
    assert retained["id"] == direct["id"]
    assert admin.get(path).status_code == 404
    assert admin.get("/api/chat/conversations/999999").status_code == 404
    assert admin.get(f"/api/chat/conversations/{2**80}").status_code == 422


def test_chat_history_pages_read_cursor_and_disabled_peer(app_factory):
    app, client, _ = app_factory("chat")
    admin = SignedIn(client)
    alice = member(admin, app, "alice")
    bob = member(admin, app, "bob")
    channel = created(admin.post("/api/chat/channels", json={"name": "release"}))
    path = f"/api/chat/conversations/{channel['id']}/messages"
    sent = [created(alice.post(path, json={"body": f"Message {index:03d}"})) for index in range(105)]
    recent = bob.get(path).json()
    assert len(recent["items"]) == 100 and recent["has_more"] is True
    assert recent["items"][-1]["id"] == sent[-1]["id"]
    earlier = bob.get(f"{path}?before_id={recent['items'][0]['id']}").json()
    assert len(earlier["items"]) == 5 and earlier["has_more"] is False
    assert {m["id"] for m in recent["items"] + earlier["items"]} == {m["id"] for m in sent}
    read = f"/api/chat/conversations/{channel['id']}/read"
    assert bob.post(read, json={"message_id": sent[-1]["id"]}).status_code == 200
    assert bob.post(read, json={"message_id": sent[0]["id"]}).status_code == 200
    channels = all_items(bob, "/api/chat/channels")
    assert next(c for c in channels if c["id"] == channel["id"])["unread_count"] == 0
    dm = created(alice.post("/api/chat/dms", json={"user_id": bob.user["id"]}))
    direct_path = f"/api/chat/conversations/{dm['id']}/messages"
    private = created(alice.post(direct_path, json={"body": "Keep history"}))
    assert alice.post(read, json={"message_id": private["id"]}).status_code in (404, 422)
    assert admin.patch(f"/api/users/{bob.user['id']}", json={"active": False}).status_code == 200
    assert alice.get(direct_path).status_code == 200
    assert alice.post(direct_path, json={"body": "Do not send to disabled peer"}).status_code == 409


@pytest.mark.parametrize("app_id", ("files", "projects", "notes", "chat"))
def test_501_resources_remain_reachable_through_cursor_pages(app_factory, app_id):
    _, client, _ = app_factory(app_id)
    admin = SignedIn(client)
    path = MODULE_ROOTS[app_id]
    for index in range(501):
        if app_id == "files":
            payload = {"name": f"Folder {index:03d}", "parent_id": None}
            result = admin.post("/api/files/folders", json=payload)
        elif app_id == "projects":
            result = admin.post(path, json={"name": f"Project {index:03d}"})
        elif app_id == "notes":
            result = admin.post(path, json={"title": f"Page {index:03d}", "body": "Retained"})
        else:
            result = admin.post(path, json={"name": f"channel-{index:03d}"})
        assert result.status_code in (200, 201), result.text
    rows = all_items(admin, path)
    # A default channel is allowed; all 501 explicitly confirmed records must survive.
    prefix = {"files": "Folder", "projects": "Project", "notes": "Page", "chat": "channel-"}[app_id]
    field = "title" if app_id == "notes" else "name"
    matching = [row for row in rows if row[field].startswith(prefix)]
    assert len(matching) == 501
    assert len({row["id"] for row in matching}) == 501


@pytest.mark.parametrize("app_id", APP_IDS)
def test_master_schema_matches_live_database_and_data_reopen(app_factory, app_id):
    _, client, data = app_factory(app_id)
    admin = SignedIn(client)
    master_path = ROOT / "apps" / app_id / "schema.sql"
    assert master_path.exists(), f"Missing source-of-truth schema: {master_path}"
    live = sqlite3.connect(data / "opensw.sqlite3")
    fresh = sqlite3.connect(":memory:")
    fresh.executescript(master_path.read_text())

    def schema(connection):
        return [
            (kind, name, " ".join(sql.split()))
            for kind, name, sql in connection.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL ORDER BY type,name"
            )
        ]

    assert schema(live) == schema(fresh)
    assert live.execute("PRAGMA user_version").fetchone()[0] == 1
    live.close()
    fresh.close()
    original_id = admin.user["id"]
    app_factory.stop(client)
    # A real lifespan shutdown/reopen supplies no second bootstrap.
    reopened = create_app(app_id=app_id, data_dir=data, config={
        "app": app_id, "instance_name": "test", "team_name": "OpenSW test team",
        "public_url": "http://testserver", "max_upload_mb": 1,
    })
    with TestClient(reopened) as next_client:
        assert SignedIn(next_client).user["id"] == original_id


def test_existing_data_cannot_be_opened_as_a_different_app(app_factory):
    _, client, data = app_factory("files")
    admin = SignedIn(client)
    created(admin.post("/api/files/folders", json={"name": "Keep me", "parent_id": None}))
    with pytest.raises((RuntimeError, ValueError)):
        wrong = create_app(app_id="notes", data_dir=data, config={
            "app": "notes", "instance_name": "test", "team_name": "Wrong app",
            "public_url": "http://testserver", "max_upload_mb": 1,
        })
        with TestClient(wrong):
            pass
    assert all_items(admin, "/api/files")[0]["name"] == "Keep me"
