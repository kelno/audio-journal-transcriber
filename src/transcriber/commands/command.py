# pyright:  reportExplicitAny=false

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, override
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from transcriber.commands import command_type  # noqa: TC001
from transcriber.commands.command_interpretation import CommandArguments, EmptyCommandArguments


class Command(BaseModel):
    """Represents a single command with execution state.

    A command is a verbal command gathered from the transcription.
    Commands are interpreted to a CommandType with structured arguments. Their
    execution frequency is controlled by the matched type's execution policy.
    """

    model_config = ConfigDict(validate_assignment=True)  # pyright: ignore[reportUnannotatedClassAttribute]

    id: str = Field(default_factory=lambda: uuid4().hex)  # Stable unique identifier for this command
    text: str  # The natural language prompt.
    executed: bool = False
    executed_at: datetime | None = None
    matched_type: command_type.CommandType | None = None
    arguments: CommandArguments = Field(default_factory=EmptyCommandArguments)
    last_error: str | None = None  # Error string to help with debugging
    attempt_count: int = Field(default=0, ge=0)  # we stop trying if reaching the max retries for that command type

    @field_serializer("executed_at")
    def serialize_executed_at(self, value: datetime | None) -> str | None:
        """Dump the date string with seconds (such as 2026-05-29T10:30:00+00:00)."""
        return value.isoformat(timespec="seconds") if value else None

    @field_serializer("arguments")
    def serialize_arguments(self, value: CommandArguments) -> dict[str, object]:
        """Serialize only arguments that have an extracted value."""
        return value.model_dump(mode="json", exclude_none=True)

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
