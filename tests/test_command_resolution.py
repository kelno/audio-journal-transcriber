"""Tests for strict typed command resolutions."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from transcriber.commands.command import Command
from transcriber.commands.command_resolution import (
    ActionRequestCommandResolution,
    MigratedCommandResolution,
    SupersededCommandResolution,
)

REQUEST_ID = "a" * 32
RESOLVED_AT = datetime(2026, 8, 6, 10, tzinfo=UTC)


def test_migrated_resolution_is_terminal_without_an_action_request() -> None:
    """The one-time migration can preserve completion without inventing intent."""
    command = Command(
        text="delete this",
        resolution=MigratedCommandResolution(resolved_at=RESOLVED_AT),
    )

    assert command.resolution == MigratedCommandResolution(resolved_at=RESOLVED_AT)
    assert command.is_resolved
    assert not command.needs_resolution


def test_legacy_execution_fields_require_store_migration() -> None:
    """Normal runtime loading rejects old state instead of silently projecting it."""
    with pytest.raises(ValidationError):
        Command.from_dict(
            {
                "text": "delete this",
                "executed": True,
                "executed_at": RESOLVED_AT.isoformat(),
            },
        )


def test_active_request_is_submitted_but_not_terminally_resolved() -> None:
    """A command awaiting its request outcome must not be dispatched again."""
    command = Command(
        text="merge this",
        resolution=ActionRequestCommandResolution(request_id=REQUEST_ID),
    )

    assert command.has_active_request
    assert not command.is_resolved
    assert not command.needs_resolution


def test_terminal_request_outcome_is_resolved() -> None:
    """An acknowledged action request is terminal command state."""
    command = Command(
        text="merge this",
        resolution=ActionRequestCommandResolution(
            request_id=REQUEST_ID,
            outcome="blocked",
            resolved_at=RESOLVED_AT,
        ),
    )

    assert command.is_resolved


def test_non_action_resolution_is_resolved() -> None:
    """A superseded command is terminal command state."""
    command = Command(
        text="use another title",
        resolution=SupersededCommandResolution(resolved_at=RESOLVED_AT),
    )

    assert command.is_resolved


def test_action_request_outcome_requires_acknowledgement_timestamp() -> None:
    """A terminal receipt cannot be persisted without when it was acknowledged."""
    with pytest.raises(ValidationError):
        ActionRequestCommandResolution(request_id=REQUEST_ID, outcome="succeeded")
