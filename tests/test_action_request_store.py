"""Tests for the SQLite action-request repository."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from transcriber.actions.action import BundleTarget, DeleteAction, MergeAction, SetTitleAction
from transcriber.actions.action_request import (
    ActionError,
    ActionRequest,
    CommandActionOrigin,
    FilesystemActionOrigin,
)
from transcriber.actions.action_request_store import (
    ACTION_REQUEST_DATABASE_FILENAME,
    ACTION_REQUEST_DATABASE_SCHEMA_VERSION,
    ACTION_REQUEST_STATE_DIRECTORY_NAME,
    ActionRequestAlreadyExistsError,
    ActionRequestImmutableFieldError,
    ActionRequestNotFoundError,
    ActionRequestStoreConstraintError,
    ActionRequestStoreReadOnlyError,
    InvalidActionRequestStoreDataError,
    SQLiteActionRequestStore,
    UnsupportedActionRequestStoreSchemaError,
    default_action_request_database_path,
)
from transcriber.files.file_system import RealFileSystemService
from transcriber.transcribe_bundle import TranscribeBundle

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fake_audio_service import FakeAudioService
    from transcriber.config import TranscribeConfig

FIRST_BUNDLE_ID = "a" * 32
SECOND_BUNDLE_ID = "b" * 32
FIRST_REQUEST_ID = "c" * 32
SECOND_REQUEST_ID = "d" * 32
COMMAND_ID = "e" * 32


def _pending_request(
    *,
    request_id: str = FIRST_REQUEST_ID,
    created_at: datetime | None = None,
) -> ActionRequest:
    """Build a deterministic pending filesystem request."""
    return ActionRequest(
        request_id=request_id,
        action=DeleteAction(bundle_id=FIRST_BUNDLE_ID),
        origin=FilesystemActionOrigin(),
        created_at=created_at or datetime(2026, 8, 5, 10, tzinfo=UTC),
    )


class TestSQLiteActionRequestStoreSchema:
    """The store owns its location and forward-only schema version."""

    def test_default_path_uses_reserved_store_directory(self, tmp_path: Path) -> None:
        """Daemon state lives below an underscore-prefixed non-bundle directory."""
        assert default_action_request_database_path(tmp_path) == (
            tmp_path / ACTION_REQUEST_STATE_DIRECTORY_NAME / ACTION_REQUEST_DATABASE_FILENAME
        )

    def test_initialization_creates_missing_parent_and_schema(self, tmp_path: Path) -> None:
        """Opening a writable store creates its parent and applies migration one."""
        database_path = tmp_path / "missing" / "nested" / "requests.sqlite3"

        SQLiteActionRequestStore(database_path)

        assert database_path.is_file()
        with sqlite3.connect(database_path) as connection:
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
            table_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'",
                )
            }
        assert schema_version == ACTION_REQUEST_DATABASE_SCHEMA_VERSION
        assert "action_requests" in table_names

    def test_default_state_directory_is_ignored_by_bundle_discovery(
        self,
        tmp_path: Path,
        fake_config: TranscribeConfig,
        fake_audio_service: FakeAudioService,
    ) -> None:
        """Creating daemon state does not manufacture an invalid bundle candidate."""
        fake_config.general.store_dir = tmp_path
        SQLiteActionRequestStore(default_action_request_database_path(tmp_path))

        bundles = TranscribeBundle.gather_existing_bundles(
            store_dir=tmp_path,
            dry_run=True,
            cleanup_bundle=False,
            config=fake_config,
            fs_service=RealFileSystemService(fake_config),
            audio_service=fake_audio_service,
        )

        assert bundles == {}

    def test_rejects_database_from_newer_schema(self, tmp_path: Path) -> None:
        """Older code must not read or rewrite a schema it does not understand."""
        database_path = tmp_path / "future.sqlite3"
        with sqlite3.connect(database_path) as connection:
            connection.execute(f"PRAGMA user_version = {ACTION_REQUEST_DATABASE_SCHEMA_VERSION + 1}")

        with pytest.raises(UnsupportedActionRequestStoreSchemaError):
            SQLiteActionRequestStore(database_path)


class TestSQLiteActionRequestStorePersistence:
    """Typed requests survive relational and JSON persistence boundaries."""

    def test_round_trip_preserves_nested_types_and_terminal_error(self, tmp_path: Path) -> None:
        """Action/origin payloads and flat error columns reconstruct one request."""
        database_path = tmp_path / "requests.sqlite3"
        store = SQLiteActionRequestStore(database_path)
        started_at = datetime(2026, 8, 5, 10, 1, tzinfo=UTC)
        finished_at = datetime(2026, 8, 5, 10, 2, tzinfo=UTC)
        request = ActionRequest(
            request_id=FIRST_REQUEST_ID,
            action=MergeAction(
                source_bundle_id=FIRST_BUNDLE_ID,
                target=BundleTarget(bundle_id=SECOND_BUNDLE_ID),
            ),
            origin=CommandActionOrigin(bundle_id=FIRST_BUNDLE_ID, command_id=COMMAND_ID),
            status="blocked",
            attempt_count=1,
            created_at=datetime(2026, 8, 5, 10, tzinfo=UTC),
            started_at=started_at,
            finished_at=finished_at,
            expires_at=finished_at + timedelta(days=7),
            error=ActionError(
                code="merge_blocked",
                message="The merge requires inspection.",
                details="A failure marker exists.",
            ),
            acknowledged_at=finished_at + timedelta(seconds=1),
        )

        store.create(request)

        assert SQLiteActionRequestStore(database_path).get(FIRST_REQUEST_ID) == request

    def test_update_changes_lifecycle(self, tmp_path: Path) -> None:
        """Repository updates persist the mutable execution envelope."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        original = _pending_request()
        store.create(original)
        started_at = datetime(2026, 8, 5, 10, 1, tzinfo=UTC)
        running = original.model_copy(
            update={"status": "running", "attempt_count": 1, "started_at": started_at},
        )

        store.update(running)

        loaded = store.get(original.request_id)
        assert loaded is not None
        assert loaded.action == original.action
        assert loaded.origin == original.origin
        assert loaded.status == "running"
        assert loaded.attempt_count == 1
        assert loaded.started_at == started_at

    @pytest.mark.parametrize(
        "immutable_update",
        [
            {"action": SetTitleAction(bundle_id=SECOND_BUNDLE_ID, title="Replacement")},
            {"origin": CommandActionOrigin(bundle_id=SECOND_BUNDLE_ID, command_id=COMMAND_ID)},
            {"created_at": datetime(2026, 8, 5, 9, tzinfo=UTC)},
        ],
    )
    def test_update_rejects_changed_immutable_fields(
        self,
        tmp_path: Path,
        immutable_update: dict[str, object],
    ) -> None:
        """Lifecycle persistence reports rather than ignores changed canonical intent."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        original = _pending_request()
        store.create(original)
        changed = original.model_copy(update=immutable_update)

        with pytest.raises(ActionRequestImmutableFieldError):
            store.update(changed)

        assert store.get(original.request_id) == original

    def test_pending_requests_are_listed_oldest_first(self, tmp_path: Path) -> None:
        """Pending retrieval provides stable FIFO input to later queue policy."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        first_created_at = datetime(2026, 8, 5, 10, tzinfo=UTC)
        first = _pending_request(request_id=FIRST_REQUEST_ID, created_at=first_created_at)
        second = _pending_request(
            request_id=SECOND_REQUEST_ID,
            created_at=first_created_at + timedelta(minutes=1),
        )
        running = ActionRequest(
            request_id="f" * 32,
            action=DeleteAction(bundle_id=SECOND_BUNDLE_ID),
            origin=FilesystemActionOrigin(),
            status="running",
            created_at=first_created_at - timedelta(minutes=1),
        )
        store.create(second)
        store.create(running)
        store.create(first)

        assert store.list_pending() == [first, second]

    def test_pending_order_compares_instants_across_offsets(self, tmp_path: Path) -> None:
        """FIFO ordering uses normalized UTC instants rather than ISO offset text."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        later = _pending_request(
            request_id=FIRST_REQUEST_ID,
            created_at=datetime(2026, 8, 5, 10, tzinfo=UTC),
        )
        earlier = _pending_request(
            request_id=SECOND_REQUEST_ID,
            created_at=datetime(2026, 8, 5, 11, tzinfo=timezone(timedelta(hours=2))),
        )
        store.create(later)
        store.create(earlier)

        assert store.list_pending() == [earlier, later]

    def test_missing_request_returns_none(self, tmp_path: Path) -> None:
        """Lookup distinguishes an absent request without manufacturing state."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")

        assert store.get(FIRST_REQUEST_ID) is None

    def test_loaded_row_is_revalidated_as_domain_model(self, tmp_path: Path) -> None:
        """Database loading cannot bypass the trusted ActionRequest boundary."""
        database_path = tmp_path / "requests.sqlite3"
        store = SQLiteActionRequestStore(database_path)
        request = _pending_request()
        store.create(request)
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "UPDATE action_requests SET action_json = ? WHERE request_id = ?",
                ('{"type":"unknown"}', request.request_id),
            )

        with pytest.raises(InvalidActionRequestStoreDataError):
            store.get(request.request_id)

    def test_update_rejects_missing_request(self, tmp_path: Path) -> None:
        """Lifecycle updates cannot silently insert a request."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")

        with pytest.raises(ActionRequestNotFoundError):
            store.update(_pending_request())

    def test_running_request_can_be_loaded_for_startup_recovery(self, tmp_path: Path) -> None:
        """The durable uniqueness invariant gives recovery at most one request."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        assert store.get_running() is None
        running = _pending_request()
        running.status = "running"
        running.attempt_count = 1
        running.started_at = datetime(2026, 8, 5, 10, 1, tzinfo=UTC)
        store.create(running)

        assert store.get_running() == running

    def test_prune_expired_deletes_only_terminal_requests(self, tmp_path: Path) -> None:
        """Retention cannot age-prune pending or running work."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        current_time = datetime(2026, 8, 12, 10, tzinfo=UTC)
        expired = _pending_request(request_id="1" * 32)
        expired.status = "succeeded"
        expired.finished_at = current_time - timedelta(days=7)
        expired.expires_at = current_time
        retained_terminal = _pending_request(request_id="2" * 32)
        retained_terminal.status = "succeeded"
        retained_terminal.finished_at = current_time
        retained_terminal.expires_at = current_time + timedelta(seconds=1)
        pending = _pending_request(request_id="3" * 32)
        pending.expires_at = current_time - timedelta(days=1)
        running = _pending_request(request_id="4" * 32)
        running.status = "running"
        running.expires_at = current_time - timedelta(days=1)
        for request in (expired, retained_terminal, pending, running):
            store.create(request)

        deleted_ids = store.prune_expired(current_time)

        assert deleted_ids == [expired.request_id]
        assert store.get(expired.request_id) is None
        assert store.get(retained_terminal.request_id) == retained_terminal
        assert store.get(pending.request_id) == pending
        assert store.get(running.request_id) == running


class TestSQLiteActionRequestStoreTransactions:
    """Constraint failures roll back without damaging canonical requests."""

    def test_duplicate_id_does_not_overwrite_existing_request(self, tmp_path: Path) -> None:
        """Request creation is insert-only and reports ID collisions."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        original = _pending_request()
        duplicate = ActionRequest(
            request_id=original.request_id,
            action=DeleteAction(bundle_id=SECOND_BUNDLE_ID),
            origin=FilesystemActionOrigin(),
        )
        store.create(original)

        with pytest.raises(ActionRequestAlreadyExistsError):
            store.create(duplicate)

        assert store.get(original.request_id) == original

    def test_failed_update_rolls_back_existing_lifecycle(self, tmp_path: Path) -> None:
        """A database constraint failure leaves the previously committed row intact."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        original = _pending_request()
        store.create(original)
        invalid_terminal_request = original.model_copy(
            update={"status": "failed", "finished_at": datetime(2026, 8, 5, 10, 1, tzinfo=UTC)},
        )

        with pytest.raises(ActionRequestStoreConstraintError):
            store.update(invalid_terminal_request)

        assert store.get(original.request_id) == original

    def test_database_enforces_one_running_request(self, tmp_path: Path) -> None:
        """The single-threaded execution invariant is durable rather than conventional."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        first = _pending_request(request_id=FIRST_REQUEST_ID)
        first.status = "running"
        second = _pending_request(request_id=SECOND_REQUEST_ID)
        second.status = "running"
        store.create(first)

        with pytest.raises(ActionRequestStoreConstraintError):
            store.create(second)

        assert store.get(FIRST_REQUEST_ID) == first
        assert store.get(SECOND_REQUEST_ID) is None


class TestSQLiteActionRequestStoreDryRun:
    """Dry-run access never creates or mutates daemon state."""

    def test_missing_database_is_empty_and_not_created(self, tmp_path: Path) -> None:
        """Inspecting a missing store in dry-run mode leaves no filesystem artifacts."""
        database_path = tmp_path / "missing" / "requests.sqlite3"
        store = SQLiteActionRequestStore(database_path, dry_run=True)

        assert store.get(FIRST_REQUEST_ID) is None
        assert store.list_pending() == []
        with pytest.raises(ActionRequestStoreReadOnlyError):
            store.create(_pending_request())
        assert not database_path.parent.exists()

    def test_existing_database_can_be_read_but_not_changed(self, tmp_path: Path) -> None:
        """Dry-run uses canonical state while rejecting lifecycle writes."""
        database_path = tmp_path / "requests.sqlite3"
        writable_store = SQLiteActionRequestStore(database_path)
        request = _pending_request()
        writable_store.create(request)
        before = database_path.read_bytes()
        read_only_store = SQLiteActionRequestStore(database_path, dry_run=True)

        assert read_only_store.get(request.request_id) == request
        with pytest.raises(ActionRequestStoreReadOnlyError):
            read_only_store.update(request)
        with pytest.raises(ActionRequestStoreReadOnlyError):
            read_only_store.prune_expired(datetime(2026, 8, 12, tzinfo=UTC))
        assert database_path.read_bytes() == before
