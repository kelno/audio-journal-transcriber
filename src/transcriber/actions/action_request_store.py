"""Repository contract and SQLite persistence for durable action requests."""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from contextlib import closing
from datetime import UTC
from typing import TYPE_CHECKING, Final, override

from pydantic import BaseModel, ValidationError

from transcriber.actions.action_request import ActionError, ActionRequest, ActionRequestId

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

ACTION_REQUEST_STATE_DIRECTORY_NAME: Final = "_state"
ACTION_REQUEST_DATABASE_FILENAME: Final = "transcriber.sqlite3"

_CREATE_SCHEMA_VERSION_1: Final = (
    """
    CREATE TABLE action_requests (
        request_id TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        action_json TEXT NOT NULL CHECK (json_valid(action_json)),
        origin_json TEXT NOT NULL CHECK (json_valid(origin_json)),
        status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'blocked')),
        attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        expires_at TEXT,
        error_code TEXT,
        error_message TEXT,
        error_details TEXT,
        acknowledged_at TEXT,
        CHECK (
            (status IN ('failed', 'blocked') AND error_code IS NOT NULL AND error_message IS NOT NULL)
            OR
            (
                status IN ('pending', 'running', 'succeeded')
                AND error_code IS NULL
                AND error_message IS NULL
                AND error_details IS NULL
            )
        )
    ) STRICT
    """,
    """
    CREATE UNIQUE INDEX one_running_action_request
    ON action_requests (status)
    WHERE status = 'running'
    """,
    """
    CREATE INDEX pending_action_requests_by_age
    ON action_requests (status, created_at, request_id)
    """,
)
_SCHEMA_MIGRATIONS: Final = (_CREATE_SCHEMA_VERSION_1,)
ACTION_REQUEST_DATABASE_SCHEMA_VERSION: Final = len(_SCHEMA_MIGRATIONS)


class ActionRequestStoreError(RuntimeError):
    """Base error raised by action-request persistence."""


class ActionRequestAlreadyExistsError(ActionRequestStoreError):
    """Raised when a request ID is already canonical in the store."""


class ActionRequestNotFoundError(ActionRequestStoreError):
    """Raised when updating a request that is not in the store."""


class ActionRequestImmutableFieldError(ActionRequestStoreError):
    """Raised when an update disagrees with canonical immutable request fields."""


class ActionRequestStoreConstraintError(ActionRequestStoreError):
    """Raised when a request conflicts with a database invariant."""


class ActionRequestStoreReadOnlyError(ActionRequestStoreError):
    """Raised when dry-run code attempts to mutate canonical state."""


class UnsupportedActionRequestStoreSchemaError(ActionRequestStoreError):
    """Raised when the database schema cannot be used by this application version."""


class InvalidActionRequestStoreDataError(ActionRequestStoreError):
    """Raised when a canonical row cannot be reconstructed as an action request."""


class ActionRequestStore(ABC):
    """Persistence boundary for durable action requests."""

    @abstractmethod
    def create(self, request: ActionRequest) -> None:
        """Persist a new canonical request without replacing an existing ID."""
        ...

    @abstractmethod
    def get(self, request_id: ActionRequestId) -> ActionRequest | None:
        """Return one request, or ``None`` when its ID is absent."""
        ...

    @abstractmethod
    def update(self, request: ActionRequest) -> None:
        """Persist mutable lifecycle fields without replacing intent or origin."""
        ...

    @abstractmethod
    def list_pending(self) -> list[ActionRequest]:
        """Return pending requests in stable oldest-first order."""
        ...

    @abstractmethod
    def get_running(self) -> ActionRequest | None:
        """Return the single running request, or ``None`` when idle."""
        ...

    @abstractmethod
    def prune_expired(self, current_time: datetime) -> list[ActionRequestId]:
        """Delete expired terminal requests and return their IDs."""
        ...


def default_action_request_database_path(store_dir: Path) -> Path:
    """Return the default daemon-owned SQLite path below a managed store."""
    return store_dir / ACTION_REQUEST_STATE_DIRECTORY_NAME / ACTION_REQUEST_DATABASE_FILENAME


class SQLiteActionRequestStore(ActionRequestStore):
    """SQLite-backed action-request repository for one application process."""

    def __init__(self, database_path: Path, *, dry_run: bool = False) -> None:
        """Open or initialize a request store at an explicit path.

        Dry-run stores read an existing compatible database without creating or
        migrating it. Their mutation methods raise instead of pretending a
        submission was durably accepted.
        """
        self.database_path: Path = database_path
        self.dry_run: bool = dry_run
        self._database_available: bool = database_path.is_file()

        if dry_run:
            if self._database_available:
                try:
                    with closing(self._connect(read_only=True)) as connection:
                        self._require_current_schema(connection)
                        self._verify_request_table(connection)
                except sqlite3.Error as error:
                    msg = f"Could not open action-request database read-only at {database_path}: {error}"
                    raise ActionRequestStoreError(msg) from error
            return

        database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(self._connect(read_only=False)) as connection:
                self._migrate(connection)
                self._verify_request_table(connection)
        except sqlite3.Error as error:
            msg = f"Could not initialize action-request database at {database_path}: {error}"
            raise ActionRequestStoreError(msg) from error
        self._database_available = True

    def _connect(self, *, read_only: bool) -> sqlite3.Connection:
        """Open a row-addressable connection in writable or enforced read-only mode."""
        if read_only:
            database_uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
            connection = sqlite3.connect(database_uri, uri=True)
            connection.execute("PRAGMA query_only = ON")
        else:
            connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _database_schema_version(connection: sqlite3.Connection) -> int:
        """Read the application-controlled schema version from SQLite metadata."""
        row = connection.execute("PRAGMA user_version").fetchone()
        if row is None:
            msg = "SQLite did not return an action-request schema version"
            raise InvalidActionRequestStoreDataError(msg)
        return int(row[0])

    def _require_current_schema(self, connection: sqlite3.Connection) -> None:
        """Reject a read-only database that does not match the current schema."""
        schema_version = self._database_schema_version(connection)
        if schema_version != ACTION_REQUEST_DATABASE_SCHEMA_VERSION:
            msg = (
                f"Action-request database schema {schema_version} cannot be used read-only; "
                f"expected {ACTION_REQUEST_DATABASE_SCHEMA_VERSION}"
            )
            raise UnsupportedActionRequestStoreSchemaError(msg)

    def _migrate(self, connection: sqlite3.Connection) -> None:
        """Apply every missing schema migration in its own immediate transaction."""
        schema_version = self._database_schema_version(connection)
        if schema_version > ACTION_REQUEST_DATABASE_SCHEMA_VERSION:
            msg = (
                f"Action-request database schema {schema_version} is newer than supported schema "
                f"{ACTION_REQUEST_DATABASE_SCHEMA_VERSION}"
            )
            raise UnsupportedActionRequestStoreSchemaError(msg)
        for target_version in range(schema_version + 1, ACTION_REQUEST_DATABASE_SCHEMA_VERSION + 1):
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in _SCHEMA_MIGRATIONS[target_version - 1]:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {target_version}")
            except Exception:
                connection.rollback()
                raise
            connection.commit()

    @staticmethod
    def _verify_request_table(connection: sqlite3.Connection) -> None:
        """Verify that the expected canonical request table can be queried."""
        try:
            connection.execute("SELECT request_id FROM action_requests LIMIT 0")
        except sqlite3.Error as error:
            msg = "Action-request database schema does not contain a readable action_requests table"
            raise InvalidActionRequestStoreDataError(msg) from error

    def _require_writable(self) -> None:
        """Reject mutation when the store was opened for a dry run."""
        if self.dry_run:
            msg = "Action-request store is read-only during dry-run"
            raise ActionRequestStoreReadOnlyError(msg)

    @staticmethod
    def _json_payload(model: BaseModel) -> str:
        """Serialize an immutable request value to deterministic compact JSON."""
        return json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _stored_datetime(value: datetime | None) -> str | None:
        """Normalize an optional aware timestamp to its UTC database form."""
        if value is None:
            return None
        return value.astimezone(UTC).isoformat()

    @classmethod
    def _request_insert_values(cls, request: ActionRequest) -> tuple[object, ...]:
        """Flatten a complete request into the action_requests insert order."""
        error = request.error
        return (
            request.request_id,
            request.schema_version,
            cls._json_payload(request.action),
            cls._json_payload(request.origin),
            request.status,
            request.attempt_count,
            cls._stored_datetime(request.created_at),
            cls._stored_datetime(request.started_at),
            cls._stored_datetime(request.finished_at),
            cls._stored_datetime(request.expires_at),
            error.code if error else None,
            error.message if error else None,
            error.details if error else None,
            cls._stored_datetime(request.acknowledged_at),
        )

    @classmethod
    def _request_update_values(cls, request: ActionRequest) -> tuple[object, ...]:
        """Flatten mutable lifecycle state into the action_requests update order."""
        error = request.error
        return (
            request.status,
            request.attempt_count,
            cls._stored_datetime(request.started_at),
            cls._stored_datetime(request.finished_at),
            cls._stored_datetime(request.expires_at),
            error.code if error else None,
            error.message if error else None,
            error.details if error else None,
            cls._stored_datetime(request.acknowledged_at),
            request.request_id,
        )

    @staticmethod
    def _changed_immutable_fields(stored: ActionRequest, updated: ActionRequest) -> list[str]:
        """Return immutable fields that disagree with the canonical stored request."""
        immutable_fields = ("schema_version", "action", "origin", "created_at")
        return [field_name for field_name in immutable_fields if getattr(stored, field_name) != getattr(updated, field_name)]

    @staticmethod
    def _row_to_request(row: sqlite3.Row) -> ActionRequest:
        """Reconstruct and validate one canonical request from a SQLite row."""
        try:
            error = None
            if row["error_code"] is not None:
                error = ActionError(
                    code=row["error_code"],
                    message=row["error_message"],
                    details=row["error_details"],
                )
            return ActionRequest.model_validate(
                {
                    "schema_version": row["schema_version"],
                    "request_id": row["request_id"],
                    "action": json.loads(row["action_json"]),
                    "origin": json.loads(row["origin_json"]),
                    "status": row["status"],
                    "attempt_count": row["attempt_count"],
                    "created_at": row["created_at"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "expires_at": row["expires_at"],
                    "error": error,
                    "acknowledged_at": row["acknowledged_at"],
                },
            )
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
            request_id = row["request_id"]
            msg = f"Invalid canonical action-request row {request_id!r}: {error}"
            raise InvalidActionRequestStoreDataError(msg) from error

    @override
    def create(self, request: ActionRequest) -> None:
        """Persist a new canonical request without replacing an existing ID."""
        self._require_writable()
        insert_sql = """
            INSERT INTO action_requests (
                request_id, schema_version, action_json, origin_json, status,
                attempt_count, created_at, started_at, finished_at, expires_at,
                error_code, error_message, error_details, acknowledged_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        try:
            with closing(self._connect(read_only=False)) as connection, connection:
                connection.execute(insert_sql, self._request_insert_values(request))
        except sqlite3.IntegrityError as error:
            if self.get(request.request_id) is not None:
                msg = f"Action request already exists: {request.request_id}"
                raise ActionRequestAlreadyExistsError(msg) from error
            msg = f"Action request violates a database invariant: {request.request_id}"
            raise ActionRequestStoreConstraintError(msg) from error
        except sqlite3.Error as error:
            msg = f"Could not create action request {request.request_id}: {error}"
            raise ActionRequestStoreError(msg) from error

    @override
    def get(self, request_id: ActionRequestId) -> ActionRequest | None:
        """Return one request, or ``None`` when its ID is absent."""
        if not self._database_available:
            return None
        try:
            with closing(self._connect(read_only=self.dry_run)) as connection:
                row = connection.execute(
                    "SELECT * FROM action_requests WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
        except sqlite3.Error as error:
            msg = f"Could not load action request {request_id}: {error}"
            raise ActionRequestStoreError(msg) from error
        return self._row_to_request(row) if row is not None else None

    @override
    def update(self, request: ActionRequest) -> None:
        """Persist mutable lifecycle fields without replacing intent or origin."""
        self._require_writable()
        update_sql = """
            UPDATE action_requests
            SET status = ?, attempt_count = ?, started_at = ?, finished_at = ?,
                expires_at = ?, error_code = ?, error_message = ?,
                error_details = ?, acknowledged_at = ?
            WHERE request_id = ?
        """
        try:
            with closing(self._connect(read_only=False)) as connection, connection:
                stored_row = connection.execute(
                    "SELECT * FROM action_requests WHERE request_id = ?",
                    (request.request_id,),
                ).fetchone()
                if stored_row is None:
                    msg = f"Action request not found: {request.request_id}"
                    raise ActionRequestNotFoundError(msg)
                stored_request = self._row_to_request(stored_row)
                if changed_fields := self._changed_immutable_fields(stored_request, request):
                    changed_fields_text = ", ".join(changed_fields)
                    msg = f"Action request update changes immutable fields ({changed_fields_text}): {request.request_id}"
                    raise ActionRequestImmutableFieldError(msg)

                cursor = connection.execute(update_sql, self._request_update_values(request))
                if cursor.rowcount == 0:
                    msg = f"Action request not found: {request.request_id}"
                    raise ActionRequestNotFoundError(msg)
        except sqlite3.IntegrityError as error:
            msg = f"Action request violates a database invariant: {request.request_id}"
            raise ActionRequestStoreConstraintError(msg) from error
        except sqlite3.Error as error:
            msg = f"Could not update action request {request.request_id}: {error}"
            raise ActionRequestStoreError(msg) from error

    @override
    def list_pending(self) -> list[ActionRequest]:
        """Return pending requests in stable oldest-first order."""
        if not self._database_available:
            return []
        try:
            with closing(self._connect(read_only=self.dry_run)) as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM action_requests
                    WHERE status = 'pending'
                    ORDER BY created_at, request_id
                    """,
                ).fetchall()
        except sqlite3.Error as error:
            msg = f"Could not list pending action requests: {error}"
            raise ActionRequestStoreError(msg) from error
        return [self._row_to_request(row) for row in rows]

    @override
    def get_running(self) -> ActionRequest | None:
        """Return the single running request, or ``None`` when idle."""
        if not self._database_available:
            return None
        try:
            with closing(self._connect(read_only=self.dry_run)) as connection:
                row = connection.execute(
                    "SELECT * FROM action_requests WHERE status = 'running'",
                ).fetchone()
        except sqlite3.Error as error:
            msg = f"Could not load the running action request: {error}"
            raise ActionRequestStoreError(msg) from error
        return self._row_to_request(row) if row is not None else None

    @override
    def prune_expired(self, current_time: datetime) -> list[ActionRequestId]:
        """Delete expired terminal requests whose origins no longer need them.

        A command request remains available until acknowledgement confirms its
        terminal receipt was written to the command file. This lets a later run
        repair the gap if the process stopped between the SQLite and YAML writes.
        """
        self._require_writable()
        cutoff = self._stored_datetime(current_time)
        try:
            with closing(self._connect(read_only=False)) as connection, connection:
                rows = connection.execute(
                    """
                    SELECT request_id FROM action_requests
                    WHERE status IN ('succeeded', 'failed', 'blocked')
                        AND expires_at <= ?
                        AND (json_extract(origin_json, '$.type') != 'command' OR acknowledged_at IS NOT NULL)
                    ORDER BY expires_at, request_id
                    """,
                    (cutoff,),
                ).fetchall()
                connection.execute(
                    """
                    DELETE FROM action_requests
                    WHERE status IN ('succeeded', 'failed', 'blocked')
                        AND expires_at <= ?
                        AND (json_extract(origin_json, '$.type') != 'command' OR acknowledged_at IS NOT NULL)
                    """,
                    (cutoff,),
                )
        except sqlite3.Error as error:
            msg = f"Could not prune expired action requests: {error}"
            raise ActionRequestStoreError(msg) from error
        return [row["request_id"] for row in rows]
