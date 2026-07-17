# pyright:  reportExplicitAny=false, reportImportCycles=false

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import transcriber.commands.command_type as command_type  # noqa: PLR0402


@dataclass
class Command:
    """Represents a single command with execution state.

    A command is a verbal command gathered from the transcription.
    Commands are interpreted to a CommandType, and only one per type can be executed for a bundle.
    (Commands might later need to support "arguments", such as a command to give the bundle a title.)
    """

    text: str # The natural language prompt. Should be unique for a bundle as it's used as identifier.
    executed: bool = False
    executed_at: datetime | None = None
    matched_type: command_type.CommandType | None = None
    # (NYI) last_error: str | None = None # Debug error string to help debugging
    # TODO: add a retry system, up to a configured amount of times. And stop trying if reached.

    def to_dict(self) -> dict[str, Any]:
        """Convert command to dictionary for YAML serialization."""
        return {
            "text": self.text,
            "executed": self.executed,
            "executed_at": self.executed_at.isoformat(timespec="seconds") if self.executed_at else None,
            "matched_type": self.matched_type.value if self.matched_type else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Command:
        """Create a Command from a dictionary (from YAML)."""
        # Import here to avoid circular dependency at module level

        executed_at = None
        if data.get("executed_at"):
            executed_at_str = data.get("executed_at")
            if isinstance(executed_at_str, str):
                executed_at = datetime.fromisoformat(executed_at_str)

        matched_type = None
        if matched_type_str := data.get("matched_type"):
            if isinstance(matched_type_str, str):
                with suppress(ValueError):
                    matched_type = command_type.CommandType(matched_type_str)

        text = data.get("text")
        executed = data.get("executed", False)
        if not isinstance(text, str) or not isinstance(executed, bool):
            msg = "Invalid command data: text must be str and executed must be bool"
            raise TypeError(msg)

        return cls(
            text=text,
            executed=executed,
            executed_at=executed_at,
            matched_type=matched_type,
            # last_error
            # retries
        )
