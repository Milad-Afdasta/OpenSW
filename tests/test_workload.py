"""Safety and verdict proofs for the real-HTTPS workload runner.

These unit tests are not workload or capacity evidence.
"""

import importlib.util
import json
from pathlib import Path
import stat
import sys

import pytest


SPEC = importlib.util.spec_from_file_location("production_workload", Path(__file__).parent / "production" / "workload.py")
workload = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = workload
SPEC.loader.exec_module(workload)


@pytest.mark.parametrize("method,status,expected,verdict", [
    ("GET", 200, (200,), "confirmed"),
    ("POST", 201, (200, 201), "confirmed"),
    ("PATCH", 409, (200, 409), "expected_rejection"),
    ("POST", 429, (401, 429), "expected_rejection"),
    ("GET", 404, (404,), "expected_rejection"),
    ("GET", 401, (200,), "unexpected"),
    ("POST", 409, (201,), "unexpected"),
    ("POST", 503, (503,), "unconfirmed_write"),
    ("POST", None, (200,), "unconfirmed_write"),
    ("GET", None, (200,), "unexpected"),
])
def test_rejections_and_unknown_mutations_have_distinct_verdicts(method, status, expected, verdict):
    assert workload.classify(method, status, expected) == verdict


def test_nearest_rank_percentiles_keep_small_sample_outliers_visible():
    assert workload.percentile([], .95) is None
    assert workload.percentile([.2, .1, 8], .5) == .2
    assert workload.percentile([.2, .1, 8], .95) == 8
    assert workload.percentile([.2] * 99 + [8], .99) == .2
    assert workload.percentile([.2] * 98 + [8, 9], .99) == 8


def test_realistic_documents_preserve_unique_unicode_markers_and_exact_utf8_byte_sizes():
    markers = ("wl-test seeded item 00042", "wl-test actor-007 edit 112 🌍", "wl-test controlled steady50 1")
    for app, expected_bytes in (("notes", 4096), ("projects", 1024)):
        values = [workload.document_text(app, marker) for marker in markers]
        assert len(set(values)) == len(markers)
        for marker, text in zip(markers, values):
            assert text.startswith(f"# {marker}\n\n")
            assert len(text.encode("utf-8")) == expected_bytes
            assert "current decisions, owner, next action, and evidence" in text
            assert workload.document_text(app, marker) == text
    with pytest.raises(ValueError):
        workload.document_text("projects", "oversized-identity" * 100)


def test_operator_and_unknown_resource_mutations_are_blocked_before_http():
    fixtures = workload.Fixtures("wl-test")
    fixtures.remember("users", {"id": 2, "username": "wl-test-000", "role": "member"})
    fixtures.remember("notes", {"id": 10, "title": "wl-test-note"})
    fixtures.admit("POST", "/api/users", {"username": "wl-test-001", "password": "public-test-only"})
    fixtures.admit("PATCH", "/api/users/2", {"active": False})
    fixtures.admit("PATCH", "/api/notes/10", {"body": "Owned update", "expected_revision": 1})
    for method, path, payload in [
        ("PATCH", "/api/users/1", {"active": False}),
        ("PATCH", "/api/users/2", {"password": "never"}),
        ("DELETE", "/api/users/2", {}),
        ("POST", "/api/users", {"username": "operator", "password": "never"}),
        ("POST", "/api/auth/password", {"new_password": "never"}),
        ("POST", "/api/auth/logout", {}),
        ("PATCH", "/api/notes/999", {"body": "Unknown update"}),
    ]:
        with pytest.raises(workload.StopRun):
            fixtures.admit(method, path, payload)
    for foreign in ({"id": 1, "username": "operator", "role": "admin"}, {"id": 1, "username": "wl-test-operator", "role": "admin"}, {"id": 3, "username": "another-run-member", "role": "member"}):
        with pytest.raises(workload.StopRun):
            fixtures.remember("users", foreign)


def test_message_markers_and_upload_parents_require_the_exact_confirmed_owner():
    fixtures = workload.Fixtures("wl-test")
    for identity in (10, 20):
        fixtures.remember("conversations", {"id": identity})
    fixtures.remember("messages", {"id": 5, "conversation_id": 10})
    fixtures.remember("files", {"id": 30, "kind": "folder"})
    fixtures.admit("POST", "/api/chat/conversations/10/read", {"message_id": 5})
    fixtures.admit("POST", "/api/files/upload", {"parent_id": 30}, filename="wl-test-owned.bin")
    for path, payload, filename in [
        ("/api/chat/conversations/20/read", {"message_id": 5}, None),
        ("/api/chat/conversations/10/read", {"message_id": 999}, None),
        ("/api/files/upload", {"parent_id": 999}, "wl-test-owned.bin"),
        ("/api/files/upload", {"parent_id": 30}, "operator.bin"),
    ]:
        with pytest.raises(workload.StopRun):
            fixtures.admit("POST", path, payload, filename)


def config_file(tmp_path):
    path = tmp_path / "private.json"
    value = {app: {"url": f"https://{app}.example.test", "username": "operator", "password": "public-unit-test-only"} for app in workload.APPS}
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path, value


def test_config_requires_one_private_flat_four_app_https_contract(tmp_path):
    path, value = config_file(tmp_path)
    assert workload.private_config(path) == value
    path.chmod(0o644)
    with pytest.raises(ValueError):
        workload.private_config(path)
    path.chmod(0o600)
    for invalid in ({"apps": value}, {**value, "foreign": {}}, {**value, "files": {**value["files"], "url": "http://files.example.test"}}, {**value, "files": {**value["files"], "url": "https://operator:password@files.example.test"}}):
        path.write_text(json.dumps(invalid))
        with pytest.raises(ValueError):
            workload.private_config(path)
    path.write_text(json.dumps(value))
    link = tmp_path / "linked.json"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        workload.private_config(link)


def test_private_report_never_overwrites_a_preexisting_file(tmp_path):
    path = tmp_path / "report.json"
    output = workload.Report(path)
    output.write({"confirmed": True})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"confirmed": True}
    with pytest.raises(FileExistsError):
        workload.Report(path)


def complete_metrics(profile):
    recorder = workload.Recorder()
    for app in workload.APPS:
        for stage in workload.STAGES[profile]:
            kinds = ("read", "write", "transfer") if app == "files" else ("read", "write")
            for kind in kinds:
                recorder.operations.append({"app": app, "stage": stage.name, "kind": kind, "seconds": .1, "status": 200, "verdict": "confirmed"})
    return recorder


def test_capacity_verdict_requires_all_operation_kinds_and_preserves_declared_budgets():
    recorder = complete_metrics("full")
    assert workload.verdict(recorder, "full", True, 2)["capacity_evidence"] is True
    recorder.operations.append({"app": "notes", "stage": "steady50", "kind": "read", "seconds": 2.01, "status": 200, "verdict": "confirmed"})
    result = workload.verdict(recorder, "full", True, 2)
    assert result["passed"] is False and result["budget_failures"]
    missing = complete_metrics("full")
    missing.operations = [item for item in missing.operations if not (item["app"] == "files" and item["stage"] == "steady50" and item["kind"] == "transfer")]
    assert workload.verdict(missing, "full", True, 2)["passed"] is False
    assert workload.verdict(complete_metrics("full"), "full", True, 30.01)["passed"] is False
    assert workload.verdict(complete_metrics("full"), "full", False, 2)["passed"] is False
    smoke = workload.verdict(complete_metrics("smoke"), "smoke", True, None)
    assert smoke["passed"] is True and smoke["capacity_evidence"] is False
    assert next(stage for stage in workload.STAGES["full"] if stage.name == "steady50").seconds == 300
    assert sum(stage.seconds for stage in workload.STAGES["smoke"]) == 30
