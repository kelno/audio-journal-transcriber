# pyright:  reportExplicitAny=false
#
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class Command:
    """Represents a single command with execution state."""

    text: str
    executed: bool = False
    executed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert command to dictionary for YAML serialization."""
        return {
            "text": self.text,
            "executed": self.executed,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Command":
        """Create a Command from a dictionary (from YAML)."""
        executed_at = None
        if data.get("executed_at"):
            executed_at_str = data.get("executed_at")
            if isinstance(executed_at_str, str):
                executed_at = datetime.fromisoformat(executed_at_str)

        text = data.get("text")
        executed = data.get("executed", False)
        if not isinstance(text, str) or not isinstance(executed, bool):
            msg = "Invalid command data: text must be str and executed must be bool"
            raise TypeError(msg)

        return cls(
            text=text,
            executed=executed,
            executed_at=executed_at,
        )
