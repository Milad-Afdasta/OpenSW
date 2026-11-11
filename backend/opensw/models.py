from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import Path
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import PRIORITY_IDS, STATUS_IDS

MAX_ID = 2**63 - 1
ResourceID = Annotated[int, Path(gt=0, le=MAX_ID)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*")
    @classmethod
    def valid_unicode(cls, value):
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError("Use valid Unicode text.") from exc
        if type(value) is int and value > MAX_ID:
            raise ValueError("This number is outside the supported integer range.")
        return value


class Login(Input):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=256)


class NewUser(Input):
    username: str
    display_name: str = Field(min_length=1, max_length=80)
    password: str


class UserActive(Input):
    active: bool


class PasswordChange(Input):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str


class Folder(Input):
    name: str
    parent_id: int | None = Field(default=None, gt=0)


class Rename(Input):
    name: str


class NewProject(Input):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=10000)


class ProjectPatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=10000)
    archived: bool | None = None


def check_status(value: str | None) -> str | None:
    if value is not None and value not in STATUS_IDS:
        raise ValueError("Select a current task status.")
    return value


def check_priority(value: str | None) -> str | None:
    if value is not None and value not in PRIORITY_IDS:
        raise ValueError("Select a current task priority.")
    return value


def check_date(value: str | None) -> str | None:
    if value is not None:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("Use a calendar date in YYYY-MM-DD format.")
    return value


class NewTask(Input):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=200000)
    status: str = "todo"
    priority: str = "normal"
    assignee_id: int | None = Field(default=None, gt=0)
    due_date: str | None = None
    _status = field_validator("status")(check_status)
    _priority = field_validator("priority")(check_priority)
    _date = field_validator("due_date")(check_date)


class TaskPatch(Input):
    expected_revision: int = Field(gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=200000)
    status: str | None = None
    priority: str | None = None
    assignee_id: int | None = Field(default=None, gt=0)
    due_date: str | None = None
    archived: bool | None = None
    _status = field_validator("status")(check_status)
    _priority = field_validator("priority")(check_priority)
    _date = field_validator("due_date")(check_date)


class NewNote(Input):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=200000)


class NotePatch(Input):
    expected_revision: int = Field(gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, max_length=200000)
    archived: bool | None = None


class Comment(Input):
    body: str = Field(min_length=1, max_length=10000)


class Channel(Input):
    name: str = Field(min_length=1, max_length=80)


class DirectMessage(Input):
    user_id: int = Field(gt=0)


class Message(Input):
    body: str = Field(min_length=1, max_length=4000)


class ReadMarker(Input):
    message_id: int = Field(gt=0)


def changes(model: BaseModel, nullable: set[str] | None = None) -> dict[str, Any]:
    nullable = nullable or set()
    result = model.model_dump(exclude_unset=True)
    result.pop("expected_revision", None)
    for key, value in result.items():
        if value is None and key not in nullable:
            raise ValueError(f"{key} cannot be null.")
        if isinstance(value, str) and key in {"name", "title", "body"} and key != "body" and not value.strip():
            raise ValueError(f"{key} cannot be blank.")
    return result
