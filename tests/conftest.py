"""Real, isolated application instances for the API invariant suite."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opensw.main import create_app


ADMIN_PASSWORD = "OpenSW-test-admin-9!"
MEMBER_PASSWORD = "OpenSW-test-member-9!"
APP_IDS = ("files", "projects", "notes", "chat")


class SignedIn:
    """A real cookie/CSRF client, with no authentication dependency overrides."""

    def __init__(self, client: TestClient, username="admin", password=ADMIN_PASSWORD):
        self.client = client
        self.origin = str(client.base_url).rstrip("/")
        response = client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers={"Origin": self.origin},
        )
        assert response.status_code == 200, response.text
        self.session = response.json()
        self.csrf = self.session["csrf_token"]

    @property
    def user(self):
        return self.session["user"]

    def request(self, method, path, **kwargs):
        headers = {"Origin": self.origin, "X-CSRF-Token": self.csrf}
        headers.update(kwargs.pop("headers", {}))
        return self.client.request(method, path, headers=headers, **kwargs)

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def patch(self, path, **kwargs):
        return self.request("PATCH", path, **kwargs)

    def delete(self, path, **kwargs):
        return self.request("DELETE", path, **kwargs)


@pytest.fixture
def app_factory(tmp_path):
    """Create one independently persisted app and any number of real users."""
    opened = []
    sequence = 0
    try:
        def make(app_id, *, data_dir=None, config_overrides=None):
            nonlocal sequence
            sequence += 1
            data_dir = Path(data_dir or tmp_path / f"{app_id}-{sequence}")
            state = data_dir / "state"
            state.mkdir(parents=True, exist_ok=True)
            # Reopening a retained database must not supply another bootstrap.
            if not (data_dir / "opensw.sqlite3").exists():
                bootstrap = state / "bootstrap.json"
                bootstrap.write_text(json.dumps({
                    "username": "admin", "password": ADMIN_PASSWORD,
                }))
                bootstrap.chmod(0o600)
            config = {
                "app": app_id,
                "instance_name": "test",
                "team_name": "OpenSW test team",
                "public_url": "http://testserver",
                "max_upload_mb": 1,
            }
            config.update(config_overrides or {})
            app = create_app(app_id=app_id, data_dir=data_dir, config=config)
            client = TestClient(app, base_url=config["public_url"])
            client.__enter__()
            opened.append(client)
            return app, client, data_dir

        def stop(client):
            client.__exit__(None, None, None)
            opened.remove(client)

        make.stop = stop
        yield make
    finally:
        for client in reversed(opened):
            client.__exit__(None, None, None)


def member(admin: SignedIn, app, name):
    response = admin.post("/api/users", json={
        "username": name,
        "display_name": name.title(),
        "password": MEMBER_PASSWORD,
    })
    assert response.status_code in (200, 201), response.text
    client = TestClient(app, base_url="http://testserver")
    return SignedIn(client, name, MEMBER_PASSWORD)


def all_items(signed: SignedIn, path: str):
    """Traverse the published cursor contract, including nonempty final pages."""
    items = []
    seen_cursors = set()
    cursor = None
    while True:
        separator = "&" if "?" in path else "?"
        url = path if cursor is None else f"{path}{separator}cursor={cursor}"
        response = signed.get(url)
        assert response.status_code == 200, response.text
        page = response.json()
        assert set(("items", "next_cursor")).issubset(page)
        assert len(page["items"]) <= 100
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return items
        assert cursor not in seen_cursors, "Collection cursor did not advance"
        seen_cursors.add(cursor)
