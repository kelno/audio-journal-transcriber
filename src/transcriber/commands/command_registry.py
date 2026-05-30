from transcriber.commands.command_handlers import (
    handle_delete,
    handle_merge,
    handle_unknown,
)
from transcriber.commands.command_metadata import CommandMetadata
from transcriber.commands.command_type import CommandType


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
