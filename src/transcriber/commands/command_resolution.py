"""Typed outcomes of interpreting and dispatching transcript commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from transcriber.actions.action_request import ActionRequestId  # noqa: TC001 - Pydantic resolves it at runtime.

if TYPE_CHECKING:
    from datetime import datetime

type ActionRequestOutcome = Literal["succeeded", "failed", "blocked"]


class CommandResolutionModel(BaseModel):
    """Immutable resolution value that rejects unknown persisted fields."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class ActionRequestCommandResolution(CommandResolutionModel):
    """Link a command to its active request or acknowledged terminal outcome."""

    type: Literal["action_request"] = "action_request"
    request_id: ActionRequestId
    outcome: ActionRequestOutcome | None = None
    resolved_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_terminal_receipt(self) -> ActionRequestCommandResolution:
        """Require the outcome and acknowledgement timestamp together."""
        if (self.outcome is None) != (self.resolved_at is None):
            msg = "Action-request command outcome and resolved_at must both be set or both be absent"
            raise ValueError(msg)
        return self


class IgnoredCommandResolution(CommandResolutionModel):
    """Record that command policy intentionally performs no action."""

    type: Literal["ignored"] = "ignored"
    resolved_at: AwareDatetime


class SupersededCommandResolution(CommandResolutionModel):
    """Record that a newer or already-applied command replaced this command."""

    type: Literal["superseded"] = "superseded"
    resolved_at: AwareDatetime


class RejectedCommandResolution(CommandResolutionModel):
    """Record that interpretation produced no supported command."""

    type: Literal["rejected"] = "rejected"
    reason: str = Field(min_length=1, max_length=1_000)
    resolved_at: AwareDatetime


class MigratedCommandResolution(CommandResolutionModel):
    """Record a completed command imported without an action-request receipt."""

    type: Literal["migrated"] = "migrated"
    resolved_at: AwareDatetime | None = None


type CommandResolution = Annotated[
    ActionRequestCommandResolution
    | IgnoredCommandResolution
    | SupersededCommandResolution
    | RejectedCommandResolution
    | MigratedCommandResolution,
    Field(discriminator="type"),
]


def resolution_time(resolution: CommandResolution) -> datetime | None:
    """Return the terminal resolution timestamp, if one is available."""
    return resolution.resolved_at


def is_terminal_resolution(resolution: CommandResolution) -> bool:
    """Return whether command processing has reached a durable terminal outcome."""
    return not isinstance(resolution, ActionRequestCommandResolution) or resolution.outcome is not None
