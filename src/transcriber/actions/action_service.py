"""Submission and synchronous lifecycle processing for action requests."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, Protocol

from transcriber.actions.action_request import (
    ActionBlocked,
    ActionError,
    ActionOrigin,
    ActionRequest,
    ActionRequestId,
    ActionResult,
    ActionSucceeded,
)
from transcriber.actions.action_request_store import (
    ActionRequestAlreadyExistsError,
    ActionRequestNotFoundError,
    ActionRequestStore,
)

if TYPE_CHECKING:
    from transcriber.actions.action import Action

ACTION_REQUEST_RETENTION: Final = timedelta(days=30)
UNEXPECTED_ACTION_ERROR_CODE: Final = "unexpected_action_error"
INTERRUPTED_ACTION_ERROR_CODE: Final = "interrupted_by_restart"

_UNEXPECTED_ACTION_MESSAGE: Final = (
    "The action stopped unexpectedly. Inspect daemon logs and durable state before submitting another action."
)
_INTERRUPTED_ACTION_MESSAGE: Final = (
    "The daemon stopped while this action was running. Inspect durable state before submitting another action."
)
_LOGGER = logging.getLogger(__name__)

type UtcClock = Callable[[], datetime]


def _utc_now() -> datetime:
    """Return the current aware UTC time for production lifecycle transitions."""
    return datetime.now(UTC)


def _read_utc_clock(clock: UtcClock) -> datetime:
    """Reject naive test or production clocks and normalize other zones to UTC."""
    current_time = clock()
    if current_time.tzinfo is None:
        msg = "Action-request clocks must return timezone-aware values"
        raise ValueError(msg)
    return current_time.astimezone(UTC)


class ActionExecutor(Protocol):
    """Apply one typed action and report its terminal outcome."""

    def execute(self, action: Action, /) -> ActionResult:
        """Execute an action synchronously without managing request lifecycle."""
        ...


@dataclass(frozen=True)
class ProcessedActionRequest:
    """A finished saved request and its temporary processing result."""

    # Request after its finished status has been saved in SQLite.
    request: ActionRequest
    # Result returned to the current processing loop, including temporary effects.
    result: ActionResult


class ActionService:
    """Transport-neutral application boundary for request submission and lookup."""

    def __init__(self, store: ActionRequestStore, *, clock: UtcClock = _utc_now) -> None:
        """Bind request operations to canonical persistence and an aware UTC clock.

        Args:
            store: Persistence boundary for canonical action requests.
            clock: Time source used for durable lifecycle timestamps.

        """
        self._store: ActionRequestStore = store
        self._clock: UtcClock = clock

    def submit(
        self,
        action: Action,
        origin: ActionOrigin,
        *,
        request_id: ActionRequestId | None = None,
    ) -> ActionRequest:
        """Durably submit intent and return its canonical request."""
        if request_id is not None and (existing := self._store.get(request_id)) is not None:
            if existing.action == action and existing.origin == origin:
                return existing
            msg = (
                f"Request ID {request_id} already exists with different request details. "
                "Use a new ID, or resend exactly the same action as the original request."
            )
            raise ActionRequestAlreadyExistsError(msg)

        request_values: dict[str, object] = {
            "action": action,
            "origin": origin,
            "created_at": _read_utc_clock(self._clock),
        }
        if request_id is not None:
            request_values["request_id"] = request_id
        request = ActionRequest.model_validate(request_values)
        self._store.create(request)
        return request

    def get_request(self, request_id: ActionRequestId) -> ActionRequest | None:
        """Return canonical request status, or ``None`` after absence or expiry."""
        return self._store.get(request_id)

    def prune_expired(self) -> list[ActionRequestId]:
        """Delete finished requests whose 30-day status window has elapsed."""
        return self._store.prune_expired(_read_utc_clock(self._clock))

    def acknowledge(self, request_id: ActionRequestId) -> None:
        """Record that a terminal outcome is durably reflected at its origin.

        Action completion is committed to SQLite before origin-specific state,
        such as a command-file receipt, is written. Those stores cannot share
        one transaction, so this separate marker closes the recoverable crash
        window and allows retention to remove the request later.
        """
        request = self._store.get(request_id)
        if request is None:
            msg = f"Action request not found: {request_id}"
            raise ActionRequestNotFoundError(msg)
        if request.status not in {"succeeded", "failed", "blocked"}:
            msg = f"Action request is not terminal and cannot be acknowledged: {request_id}"
            raise ValueError(msg)
        if request.acknowledged_at is not None:
            return
        acknowledged = request.model_copy(deep=True)
        acknowledged.acknowledged_at = _read_utc_clock(self._clock)
        self._store.update(acknowledged)


class ActionProcessor:
    """Advance durable requests through one synchronous execution attempt."""

    def __init__(
        self,
        store: ActionRequestStore,
        executor: ActionExecutor,
        *,
        clock: UtcClock = _utc_now,
    ) -> None:
        """Bind lifecycle transitions to persistence and one action executor.

        Args:
            store: Persistence boundary updated around execution.
            executor: Mutation boundary that applies typed action intent.
            clock: Time source used for running and terminal timestamps.

        """
        self._store: ActionRequestStore = store
        self._executor: ActionExecutor = executor
        self._clock: UtcClock = clock

    def process(self, request_id: ActionRequestId) -> ProcessedActionRequest | None:
        """Execute a pending request once and return its saved request and result.

        Args:
            request_id: ID of the request to execute.

        Returns:
            ProcessedActionRequest | None: The saved finished request and its
                temporary result, or None when the request is not pending.

        Raises:
            ActionRequestNotFoundError: If no request has this ID.

        """
        request = self._store.get(request_id)
        if request is None:
            msg = f"Action request not found: {request_id}"
            raise ActionRequestNotFoundError(msg)
        if request.status != "pending":
            return None

        running = request.model_copy(deep=True)
        running.status = "running"
        running.attempt_count += 1
        running.started_at = _read_utc_clock(self._clock)
        self._store.update(running)

        try:
            result = self._executor.execute(running.action)
        except Exception:
            _LOGGER.exception(
                "Unexpected error while executing action request %s (%s)",
                running.request_id,
                running.action.type,
            )
            result = ActionBlocked(
                error=ActionError(
                    code=UNEXPECTED_ACTION_ERROR_CODE,
                    message=_UNEXPECTED_ACTION_MESSAGE,
                ),
            )

        return self._finish(running, result)

    def block_interrupted_request(self) -> ProcessedActionRequest | None:
        """Safely finish work left running by an earlier daemon process.

        Returns:
            ProcessedActionRequest | None: The saved blocked request and its
                result, or None when no request was left running.

        """
        running = self._store.get_running()
        if running is None:
            return None
        result = ActionBlocked(
            error=ActionError(
                code=INTERRUPTED_ACTION_ERROR_CODE,
                message=_INTERRUPTED_ACTION_MESSAGE,
            ),
        )
        return self._finish(running, result)

    def _finish(self, running: ActionRequest, result: ActionResult) -> ProcessedActionRequest:
        """Save one finished request and return it with its processing result.

        Args:
            running: Saved request state immediately before finishing.
            result: Result returned by the action executor or restart handling.

        Returns:
            ProcessedActionRequest: The saved finished request and temporary result.

        """
        finished_at = _read_utc_clock(self._clock)
        terminal = running.model_copy(deep=True)
        terminal.status = result.status
        terminal.finished_at = finished_at
        terminal.expires_at = finished_at + ACTION_REQUEST_RETENTION
        if isinstance(result, ActionSucceeded):
            terminal.error = None
        else:
            terminal.error = result.error
        # Effects remain temporary instructions for the current processing loop.
        # Persist them here only if request status or the API should expose affected bundles later.
        self._store.update(terminal)
        return ProcessedActionRequest(request=terminal, result=result)
