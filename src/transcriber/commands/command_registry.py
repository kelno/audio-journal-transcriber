from transcriber.commands.command_definition import CommandDefinition
from transcriber.commands.command_execution_policy import CommandExecutionPolicy
from transcriber.commands.command_handlers import (
    handle_delete,
    handle_ignore,
    handle_merge,
    handle_set_title,
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
            max_attempts=2,
            handler=handle_merge,
        ),
        CommandType.DELETE: CommandDefinition(
            command_type=CommandType.DELETE,
            description="Remove or delete the current recording.",
            aliases=["remove", "discard", "trash"],
            max_attempts=2,
            handler=handle_delete,
        ),
        CommandType.SET_TITLE: CommandDefinition(
            command_type=CommandType.SET_TITLE,
            description="Set an explicit title for the current recording.",
            aliases=["title", "name", "set title"],
            max_attempts=2,
            handler=handle_set_title,
            execution_policy=CommandExecutionPolicy.LATEST_PENDING,
            argument_instructions=('Include exactly one string field, "title", containing the requested title'),
        ),
        CommandType.UNKNOWN: CommandDefinition(
            command_type=CommandType.UNKNOWN,
            description="Unknown or unmatched command.",
            handler=handle_unknown,
            max_attempts=1,
        ),
        CommandType.IGNORE: CommandDefinition(
            command_type=CommandType.IGNORE,
            description="User asks for the command to be ignored.",
            handler=handle_ignore,
            max_attempts=1,
        ),
    }


# Command metadata for LLM matching
COMMAND_REGISTRY: dict[CommandType, CommandDefinition] = _build_registry()
