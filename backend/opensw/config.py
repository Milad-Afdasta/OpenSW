from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
CATALOG = json.loads((ROOT / "shared" / "catalog.json").read_text())
APP_IDS = {item["id"] for item in CATALOG["apps"]}
STATUS_IDS = {item["id"] for item in CATALOG["task_statuses"]}
PRIORITY_IDS = {item["id"] for item in CATALOG["task_priorities"]}


@dataclass(frozen=True)
class Settings:
    app: str
    data_dir: Path
    instance_name: str
    team_name: str
    public_url: str
    max_upload_mb: int

    @classmethod
    def load(cls, app: str, data_dir: Path, config: dict | None = None) -> Settings:
        if app not in APP_IDS:
            raise RuntimeError("Select one known OpenSW application.")
        if config is None:
            path = data_dir / "state" / "config.json"
            try:
                config = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                raise RuntimeError("OpenSW configuration is missing or invalid; run the installer.") from exc
        expected = {"app", "instance_name", "team_name", "public_url", "max_upload_mb"}
        if set(config) != expected:
            raise RuntimeError("OpenSW runtime configuration has missing or unknown fields.")
        if config["app"] != app:
            raise RuntimeError("This data/configuration belongs to a different OpenSW app.")
        instance = config["instance_name"]
        if not isinstance(instance, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", instance):
            raise RuntimeError("The instance name must use 1–40 lowercase letters, digits or hyphens.")
        team = config["team_name"]
        if not isinstance(team, str) or not team.strip() or len(team) > 80:
            raise RuntimeError("The team name must contain 1–80 characters.")
        address = config["public_url"]
        if not isinstance(address, str):
            raise RuntimeError("The application address is invalid.")
        parsed = urlsplit(address)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise RuntimeError("The application address must be one HTTP(S) origin without a path.")
        try:
            parsed.port
        except ValueError as exc:
            raise RuntimeError("The application address has an invalid port.") from exc
        limit = config["max_upload_mb"]
        if type(limit) is not int or not 1 <= limit <= 1024:
            raise RuntimeError("The upload limit must be between 1 and 1024 MiB.")
        return cls(app, data_dir, instance, team, address.rstrip("/"), limit)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "opensw.sqlite3"

    @property
    def cookie_name(self) -> str:
        return f"opensw_{self.app}_{self.instance_name}_session"

    @property
    def secure_cookie(self) -> bool:
        return self.public_url.startswith("https://")
