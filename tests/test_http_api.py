"""Integration tests for the FastAPI action-request transport."""

from pathlib import Path

import requests

from transcriber.actions.action_request_store import SQLiteActionRequestStore
from transcriber.actions.action_service import ActionService
from transcriber.actions.http_api import ActionHttpServer
from transcriber.config import HttpConfig


def test_http_config_accepts_non_loopback_host() -> None:
    """Deployments may expose the API through a container network interface."""
    assert HttpConfig(enabled=True, host="0.0.0.0").host == "0.0.0.0"  # noqa: S104 - intended container bind


def test_http_submit_is_idempotent_and_status_is_queryable(tmp_path: Path) -> None:
    """A caller-provided ID makes transport retries safe and exposes canonical state."""
    service = ActionService(SQLiteActionRequestStore(tmp_path / "requests.sqlite3"))
    submissions: list[None] = []
    server = ActionHttpServer("127.0.0.1", 0, service, lambda: submissions.append(None))
    server.start()
    host, port = server.address
    base_url = f"http://{host}:{port}"
    request_id = "a" * 32
    payload = {
        "request_id": request_id,
        "action": {
            "type": "set_title",
            "bundle_id": "b" * 32,
            "title": "Planning",
        },
    }

    try:
        health = requests.get(f"{base_url}/health", timeout=5)
        first = requests.post(f"{base_url}/requests", json=payload, timeout=5)
        repeated = requests.post(f"{base_url}/requests", json=payload, timeout=5)
        status = requests.get(f"{base_url}/requests/{request_id}", timeout=5)
    finally:
        server.stop()

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert first.status_code == 202
    assert first.headers["Location"] == f"/requests/{request_id}"
    assert repeated.status_code == 202
    assert status.status_code == 200
    assert status.json()["request_id"] == request_id
    assert status.json()["status"] == "pending"
    assert status.json()["origin"] == {"type": "http"}
    assert len(submissions) == 2


def test_http_rejects_invalid_action_and_request_id_conflicts(tmp_path: Path) -> None:
    """Validation failures are client errors and IDs cannot change intent."""
    service = ActionService(SQLiteActionRequestStore(tmp_path / "requests.sqlite3"))
    server = ActionHttpServer("127.0.0.1", 0, service, lambda: None)
    server.start()
    host, port = server.address
    url = f"http://{host}:{port}/requests"
    request_id = "c" * 32

    try:
        invalid = requests.post(url, json={"action": {"type": "delete"}}, timeout=5)
        accepted = requests.post(
            url,
            json={
                "request_id": request_id,
                "action": {"type": "delete", "bundle_id": "d" * 32},
            },
            timeout=5,
        )
        conflict = requests.post(
            url,
            json={
                "request_id": request_id,
                "action": {"type": "delete", "bundle_id": "e" * 32},
            },
            timeout=5,
        )
    finally:
        server.stop()

    assert invalid.status_code == 422
    assert invalid.json()["detail"]
    assert accepted.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "request_id_conflict"
    assert conflict.json()["message"] == (
        f"Request ID {request_id} already exists with different request details. "
        "Use a new ID, or resend exactly the same action as the original request."
    )
