"""Tests for action submission, execution lifecycle, and restart safety."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from transcriber.actions.action import Action, DeleteAction
from transcriber.actions.action_request import (
    ActionBlocked,
    ActionEffects,
    ActionError,
    ActionFailed,
    ActionRequest,
    ActionResult,
    ActionSucceeded,
    HttpActionOrigin,
)
from transcriber.actions.action_request_store import (
    ActionRequestNotFoundError,
    ActionRequestStoreConstraintError,
    SQLiteActionRequestStore,
)
from transcriber.actions.action_service import (
    ACTION_REQUEST_RETENTION,
    INTERRUPTED_ACTION_ERROR_CODE,
    UNEXPECTED_ACTION_ERROR_CODE,
    ActionProcessor,
    ActionService,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

FIRST_BUNDLE_ID = "a" * 32
SECOND_BUNDLE_ID = "b" * 32
FIRST_REQUEST_ID = "c" * 32
SECOND_REQUEST_ID = "d" * 32
INITIAL_TIME = datetime(2026, 8, 5, 10, tzinfo=UTC)


class FakeClock:
    """Controllable aware clock for exact lifecycle timestamps."""

    def __init__(self, current_time: datetime = INITIAL_TIME) -> None:
        self.current_time: datetime = current_time

    def __call__(self) -> datetime:
        """Return the controlled current time."""
        return self.current_time

    def advance(self, delta: timedelta) -> None:
        """Move the next observed time forward."""
        self.current_time += delta


class FakeActionExecutor:
    """Return or raise a configured outcome while recording executions."""

    def __init__(
        self,
        result: ActionResult | None = None,
        *,
        exception: Exception | None = None,
        on_execute: Callable[[], None] | None = None,
    ) -> None:
        self.result: ActionResult = result or ActionSucceeded()
        self.exception: Exception | None = exception
        self.on_execute: Callable[[], None] | None = on_execute
        self.actions: list[Action] = []

    def execute(self, action: Action, /) -> ActionResult:
        """Record the action, run the hook, then raise or return as configured."""
        self.actions.append(action)
        if self.on_execute is not None:
            self.on_execute()
        if self.exception is not None:
            raise self.exception
        return self.result


def _delete_action() -> DeleteAction:
    return DeleteAction(bundle_id=FIRST_BUNDLE_ID)


def _running_request() -> ActionRequest:
    return ActionRequest(
        request_id=FIRST_REQUEST_ID,
        action=_delete_action(),
        origin=HttpActionOrigin(),
        status="running",
        attempt_count=1,
        created_at=INITIAL_TIME,
        started_at=INITIAL_TIME + timedelta(minutes=1),
    )


class TestActionService:
    """Submission and retention remain independent of the origin transport."""

    def test_submit_persists_new_pending_request(self, tmp_path: Path) -> None:
        """Submission stores transport-neutral intent without executing it."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        service = ActionService(store, clock=FakeClock())
        action = _delete_action()
        origin = HttpActionOrigin()

        request = service.submit(action, origin)

        assert request.action == action
        assert request.origin == origin
        assert request.status == "pending"
        assert request.attempt_count == 0
        assert request.created_at == INITIAL_TIME
        assert service.get_request(request.request_id) == request

    def test_repeating_the_same_action_creates_new_intent(self, tmp_path: Path) -> None:
        """Equivalent user submissions remain distinct durable requests."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        service = ActionService(store, clock=FakeClock())
        action = _delete_action()
        origin = HttpActionOrigin()

        first_request = service.submit(action, origin)
        second_request = service.submit(action, origin)

        assert first_request.request_id != second_request.request_id
        assert service.get_request(first_request.request_id) == first_request
        assert service.get_request(second_request.request_id) == second_request

    def test_repeating_an_explicit_id_returns_the_existing_request(self, tmp_path: Path) -> None:
        """An idempotent retry returns the canonical object already in storage."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        clock = FakeClock()
        service = ActionService(store, clock=clock)
        action = _delete_action()
        origin = HttpActionOrigin()

        first_request = service.submit(action, origin, request_id=FIRST_REQUEST_ID)
        clock.advance(timedelta(hours=1))
        repeated_request = service.submit(action, origin, request_id=FIRST_REQUEST_ID)

        assert repeated_request == first_request
        assert repeated_request.created_at == INITIAL_TIME

    def test_prune_expired_returns_removed_request_ids(self, tmp_path: Path) -> None:
        """Service cleanup exposes IDs needed by later status projections."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        clock = FakeClock(INITIAL_TIME + ACTION_REQUEST_RETENTION)
        service = ActionService(store, clock=clock)
        terminal = ActionRequest(
            request_id=FIRST_REQUEST_ID,
            action=_delete_action(),
            origin=HttpActionOrigin(),
            status="succeeded",
            created_at=INITIAL_TIME,
            finished_at=INITIAL_TIME,
            expires_at=clock.current_time,
        )
        store.create(terminal)

        assert service.prune_expired() == [terminal.request_id]
        assert service.get_request(terminal.request_id) is None


class TestActionProcessor:
    """The processor durably brackets one synchronous executor attempt."""

    def test_success_transitions_through_running_and_returns_effects(self, tmp_path: Path) -> None:
        """Success persists lifecycle timestamps while returning scheduler effects."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        clock = FakeClock()
        service = ActionService(store, clock=clock)
        result = ActionSucceeded(effects=ActionEffects(changed_bundle_ids=(FIRST_BUNDLE_ID,)))
        executor = FakeActionExecutor(result, on_execute=lambda: clock.advance(timedelta(minutes=2)))
        processor = ActionProcessor(store, executor, clock=clock)
        request_id = service.submit(_delete_action(), HttpActionOrigin()).request_id

        actual_result = processor.process(request_id)

        assert actual_result == result
        assert executor.actions == [_delete_action()]
        request = service.get_request(request_id)
        assert request is not None
        assert request.status == "succeeded"
        assert request.attempt_count == 1
        assert request.started_at == INITIAL_TIME
        assert request.finished_at == INITIAL_TIME + timedelta(minutes=2)
        assert request.finished_at is not None
        assert request.expires_at == request.finished_at + timedelta(days=30)
        assert request.error is None

    @pytest.mark.parametrize(
        "result",
        [
            ActionFailed(error=ActionError(code="invalid_target", message="The target is invalid.")),
            ActionBlocked(error=ActionError(code="unsafe_state", message="The action requires inspection.")),
        ],
    )
    def test_executor_terminal_error_is_persisted(
        self,
        tmp_path: Path,
        result: ActionFailed | ActionBlocked,
    ) -> None:
        """Expected failed and blocked outcomes persist their bounded error."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        service = ActionService(store, clock=FakeClock())
        processor = ActionProcessor(store, FakeActionExecutor(result), clock=FakeClock())
        request_id = service.submit(_delete_action(), HttpActionOrigin()).request_id

        assert processor.process(request_id) == result

        request = service.get_request(request_id)
        assert request is not None
        assert request.status == result.status
        assert request.error == result.error
        assert request.attempt_count == 1

    def test_terminal_request_is_not_executed_again(self, tmp_path: Path) -> None:
        """Repeated processing cannot create another executor attempt."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        service = ActionService(store, clock=FakeClock())
        executor = FakeActionExecutor()
        processor = ActionProcessor(store, executor, clock=FakeClock())
        request_id = service.submit(_delete_action(), HttpActionOrigin()).request_id
        processor.process(request_id)

        assert processor.process(request_id) is None
        assert executor.actions == [_delete_action()]

    def test_missing_request_is_reported_without_execution(self, tmp_path: Path) -> None:
        """An unknown ID fails before reaching the executor."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        executor = FakeActionExecutor()
        processor = ActionProcessor(store, executor, clock=FakeClock())

        with pytest.raises(ActionRequestNotFoundError):
            processor.process(FIRST_REQUEST_ID)

        assert executor.actions == []

    def test_existing_running_request_prevents_another_attempt(self, tmp_path: Path) -> None:
        """The database invariant stops execution before a second action starts."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        store.create(_running_request())
        pending = ActionRequest(
            request_id=SECOND_REQUEST_ID,
            action=DeleteAction(bundle_id=SECOND_BUNDLE_ID),
            origin=HttpActionOrigin(),
            created_at=INITIAL_TIME,
        )
        store.create(pending)
        executor = FakeActionExecutor()
        processor = ActionProcessor(store, executor, clock=FakeClock())

        with pytest.raises(ActionRequestStoreConstraintError):
            processor.process(pending.request_id)

        assert executor.actions == []
        assert store.get(pending.request_id) == pending

    def test_unexpected_exception_is_logged_and_safely_blocked(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Unexpected details stay in logs while the request safely blocks."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        service = ActionService(store, clock=FakeClock())
        executor = FakeActionExecutor(exception=RuntimeError("internal diagnostic detail"))
        processor = ActionProcessor(store, executor, clock=FakeClock())
        request_id = service.submit(_delete_action(), HttpActionOrigin()).request_id

        with caplog.at_level(logging.ERROR):
            result = processor.process(request_id)

        assert isinstance(result, ActionBlocked)
        assert result.error.code == UNEXPECTED_ACTION_ERROR_CODE
        assert "internal diagnostic detail" not in result.error.message
        assert "internal diagnostic detail" in caplog.text
        request = service.get_request(request_id)
        assert request is not None
        assert request.status == "blocked"
        assert request.error == result.error

    def test_startup_blocks_interrupted_request_without_executing_again(self, tmp_path: Path) -> None:
        """Restart handling terminates the old attempt without action-specific recovery."""
        store = SQLiteActionRequestStore(tmp_path / "requests.sqlite3")
        running = _running_request()
        store.create(running)
        clock = FakeClock(INITIAL_TIME + timedelta(hours=1))
        executor = FakeActionExecutor()
        processor = ActionProcessor(store, executor, clock=clock)

        result = processor.block_interrupted_request()

        assert isinstance(result, ActionBlocked)
        assert result.error.code == INTERRUPTED_ACTION_ERROR_CODE
        assert executor.actions == []
        blocked = store.get(running.request_id)
        assert blocked is not None
        assert blocked.status == "blocked"
        assert blocked.attempt_count == running.attempt_count
        assert blocked.started_at == running.started_at
        assert blocked.finished_at == clock.current_time
        assert blocked.expires_at == clock.current_time + timedelta(days=30)
        assert processor.block_interrupted_request() is None
