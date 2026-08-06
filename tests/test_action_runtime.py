"""Tests for run-scoped action service composition."""

from datetime import datetime, timedelta
from pathlib import Path

from tests.bundle_fixtures import TranscribeBundleFactory
from transcriber.actions.action import DeleteAction, SetTitleAction
from transcriber.actions.action_request import ActionRequest, CommandActionOrigin, HttpActionOrigin
from transcriber.actions.action_request_store import SQLiteActionRequestStore
from transcriber.actions.action_runtime import ActionRuntime
from transcriber.config import TranscribeConfig


def test_runtime_processes_http_requests_oldest_first_and_leaves_command_requests_to_commands(
    tmp_path: Path,
    fake_config: TranscribeConfig,
    transcribe_bundle_factory: TranscribeBundleFactory,
) -> None:
    """One runtime coordinates external work without stealing command acknowledgement."""
    bundle = transcribe_bundle_factory(
        bundle_name="recording",
        audio_filename="recording.mp3",
    )
    bundle_cache = {bundle.bundle_id: bundle}
    created_at = datetime(2026, 8, 6, 9, tzinfo=fake_config.general.timezone)
    store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
    command_request = ActionRequest(
        request_id="a" * 32,
        action=DeleteAction(bundle_id=bundle.bundle_id),
        origin=CommandActionOrigin(bundle_id=bundle.bundle_id, command_id="command-1"),
        created_at=created_at,
    )
    older_title = ActionRequest(
        request_id="b" * 32,
        action=SetTitleAction(bundle_id=bundle.bundle_id, title="Older title"),
        origin=HttpActionOrigin(),
        created_at=created_at + timedelta(minutes=1),
    )
    newer_title = ActionRequest(
        request_id="c" * 32,
        action=SetTitleAction(bundle_id=bundle.bundle_id, title="Newer title"),
        origin=HttpActionOrigin(),
        created_at=created_at + timedelta(minutes=2),
    )
    for request in (newer_title, command_request, older_title):
        store.create(request)

    results = ActionRuntime(store).process_external_requests(bundle_cache)

    assert len(results) == 2
    assert bundle.bundle_name.endswith("Newer title")
    assert bundle.bundle_id in bundle_cache
    assert store.get(command_request.request_id) == command_request
