from transcriber.commands.command_definition import CommandDefinition
from transcriber.commands.command_handlers import (
    handle_delete,
    handle_ignore,
    handle_merge,
    handle_unknown,
)
from transcriber.commands.command_type import CommandType


def _build_registry() -> dict[CommandType, CommandDefinition]:
    """Build the command registry, importing handlers at runtime to avoid cycles."""
    # Import here to avoid circular dependencies at module load time

    return {
        CommandType.MERGE: CommandDefinition(
            command_type=CommandType.MERGE,
            description="Combine the current recording with the previous one.",
            aliases=["merge", "join", "concatenate", "add", "fusionner"],
            handler=handle_merge,
        ),
        CommandType.DELETE: CommandDefinition(
            command_type=CommandType.DELETE,
            description="Remove or delete the current recording.",
            aliases=["remove", "discard", "trash"],
            handler=handle_delete,
        ),
        CommandType.UNKNOWN: CommandDefinition(
            command_type=CommandType.UNKNOWN,
            description="Unknown or unmatched command.",
            handler=handle_unknown,
        ),
        CommandType.IGNORE: CommandDefinition(
            command_type=CommandType.IGNORE,
            description="User asks for the command to be ignored.",
            handler=handle_ignore,
        ),
    }


# Command metadata for LLM matching
COMMAND_REGISTRY: dict[CommandType, CommandDefinition] = _build_registry()
