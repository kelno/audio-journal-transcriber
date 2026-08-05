"""Tests for typed command resolutions and legacy execution compatibility."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from transcriber.commands.command import Command
from transcriber.commands.command_resolution import (
    ActionRequestCommandResolution,
    LegacyExecutedCommandResolution,
    SupersededCommandResolution,
)

REQUEST_ID = "a" * 32
RESOLVED_AT = datetime(2026, 8, 6, 10, tzinfo=UTC)


def test_legacy_executed_fields_map_to_compatibility_resolution() -> None:
    """Existing command files acquire a typed terminal meaning when loaded."""
    command = Command.from_dict(
        {
            "text": "delete this",
            "executed": True,
            "executed_at": RESOLVED_AT.isoformat(),
        },
    )

    assert command.resolution == LegacyExecutedCommandResolution(resolved_at=RESOLVED_AT)
    assert command.is_resolved
    assert not command.needs_resolution


def test_active_request_is_submitted_but_not_terminally_resolved() -> None:
    """A command awaiting its request outcome must not be dispatched again."""
    command = Command(
        text="merge this",
        resolution=ActionRequestCommandResolution(request_id=REQUEST_ID),
    )

    assert not command.executed
    assert command.executed_at is None
    assert command.has_active_request
    assert not command.is_resolved
    assert not command.needs_resolution


def test_terminal_request_outcome_projects_to_legacy_execution_fields() -> None:
    """Old readers still see an acknowledged action request as executed."""
    command = Command(
        text="merge this",
        resolution=ActionRequestCommandResolution(
            request_id=REQUEST_ID,
            outcome="blocked",
            resolved_at=RESOLVED_AT,
        ),
    )

    assert command.executed
    assert command.executed_at == RESOLVED_AT
    assert command.is_resolved


def test_non_action_resolution_projects_to_legacy_execution_fields() -> None:
    """Superseded commands remain complete to old and new scheduling policy."""
    command = Command(
        text="use another title",
        resolution=SupersededCommandResolution(resolved_at=RESOLVED_AT),
    )

    assert command.executed
    assert command.executed_at == RESOLVED_AT
    assert command.is_resolved


def test_action_request_outcome_requires_acknowledgement_timestamp() -> None:
    """A terminal receipt cannot be persisted without when it was acknowledged."""
    with pytest.raises(ValidationError):
        ActionRequestCommandResolution(request_id=REQUEST_ID, outcome="succeeded")
