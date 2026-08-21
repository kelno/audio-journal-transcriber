# pyright:  reportExplicitAny=false

from __future__ import annotations

from typing import Any, override
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from transcriber.commands import command_type  # noqa: TC001
from transcriber.commands.command_interpretation import CommandArguments, EmptyCommandArguments
from transcriber.commands.command_resolution import (
    CommandResolution,
    is_terminal_resolution,
)


class Command(BaseModel):
    """Represents a single command with execution state.

    A command is a verbal command gathered from the transcription.
    Commands are interpreted to a CommandType with structured arguments. Their
    execution frequency is controlled by the matched type's execution policy.
    """

    model_config = ConfigDict(validate_assignment=True, extra="forbid")  # pyright: ignore[reportUnannotatedClassAttribute]

    id: str = Field(default_factory=lambda: uuid4().hex)  # Stable unique identifier for this command
    text: str  # The natural language prompt.
    matched_type: command_type.CommandType | None = None
    arguments: CommandArguments = Field(default_factory=EmptyCommandArguments)
    resolution: CommandResolution | None = None

    @field_serializer("arguments")
    def serialize_arguments(self, value: CommandArguments) -> dict[str, object]:
        """Serialize only arguments that have an extracted value."""
        return value.model_dump(mode="json", exclude_none=True)

    @property
    def is_resolved(self) -> bool:
        """Return whether the command has a terminal resolution."""
        return self.resolution is not None and is_terminal_resolution(self.resolution)

    @property
    def has_active_request(self) -> bool:
        """Return whether a submitted action request still lacks an outcome."""
        return self.resolution is not None and not is_terminal_resolution(self.resolution)

    @property
    def needs_resolution(self) -> bool:
        """Return whether command policy should consider this command again."""
        return not self.is_resolved and not self.has_active_request

    @property
    def needs_processing(self) -> bool:
        """Return whether dispatch or request reconciliation still has work."""
        return not self.is_resolved

    def to_dict(self) -> dict[str, Any]:
        """Convert command to dictionary for YAML serialization."""
        # Use JSON mode to convert non-primitive types (datetime, enum) into
        # YAML-friendly values instead of Python-specific objects.
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Command:
        """Create a Command from a dictionary (from YAML)."""
        return cls.model_validate(data)

    @override
    def __str__(self) -> str:
        """Return a string representation of the job."""
        return f"[{self.__class__.__name__}:{self.id}:{self.text}]"
