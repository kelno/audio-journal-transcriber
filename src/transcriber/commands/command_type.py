"""Command interpreter for processing transcription commands.

Defines command types and metadata for matching user intents to specific actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from transcriber.commands.command_handlers import (
    handle_delete,
    handle_merge,
    handle_unknown,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from transcriber.config import TranscribeConfig
    from transcriber.transcribe_bundle import TranscribeBundle


class CommandType(Enum):
    """Enumeration of all possible command types."""

    MERGE = "merge"
    DELETE = "delete"
    UNKNOWN = "unknown"


@dataclass
class CommandMetadata:
    """Metadata for a command type to aid in LLM matching.

    Attributes:
        command_type: The type of command to execute.
        description: Description for LLM to understand the command's purpose.
        aliases: Optional list of alternative names for this command.
        handler: Function to execute the command. Takes bundle and config.

    """

    command_type: CommandType
    description: str
    aliases: list[str] | None = None
    handler: Callable[[TranscribeBundle, TranscribeConfig], None] | None = None


def _build_registry() -> dict[CommandType, CommandMetadata]:
    """Build the command registry, importing handlers at runtime to avoid cycles."""
    # Import here to avoid circular dependencies at module load time

    return {
        CommandType.MERGE: CommandMetadata(
            command_type=CommandType.MERGE,
            description="Combine or merge the current recording with the previous one.",
            aliases=["combine", "join", "concatenate"],
            handler=handle_merge,
        ),
        CommandType.DELETE: CommandMetadata(
            command_type=CommandType.DELETE,
            description="Remove or delete the current recording.",
            aliases=["remove", "discard", "trash"],
            handler=handle_delete,
        ),
        CommandType.UNKNOWN: CommandMetadata(
            command_type=CommandType.UNKNOWN,
            description="Unknown or unmatched command.",
            handler=handle_unknown,
        ),
    }


# Command metadata for LLM matching
COMMAND_REGISTRY: dict[CommandType, CommandMetadata] = _build_registry()
