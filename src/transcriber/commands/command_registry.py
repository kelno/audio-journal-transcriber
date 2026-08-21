from transcriber.commands.command_definition import CommandDefinition
from transcriber.commands.command_execution_policy import CommandExecutionPolicy
from transcriber.commands.command_type import CommandType


def _build_registry() -> dict[CommandType, CommandDefinition]:
    """Build the command registry, importing handlers at runtime to avoid cycles."""
    # Import here to avoid circular dependencies at module load time

    return {
        CommandType.MERGE: CommandDefinition(
            command_type=CommandType.MERGE,
            description="Combine the current recording with the previous one.",
            aliases=["merge", "join", "concatenate", "add", "fusionner"],
        ),
        CommandType.DELETE: CommandDefinition(
            command_type=CommandType.DELETE,
            description="Remove or delete the current recording.",
            aliases=["remove", "discard", "trash"],
        ),
        CommandType.SET_TITLE: CommandDefinition(
            command_type=CommandType.SET_TITLE,
            description="Set an explicit title for the current recording.",
            aliases=["title", "name", "set title"],
            execution_policy=CommandExecutionPolicy.LATEST_PENDING,
            argument_instructions=('Include exactly one string field, "title", containing the requested title'),
        ),
        CommandType.UNKNOWN: CommandDefinition(
            command_type=CommandType.UNKNOWN,
            description="Unknown or unmatched command.",
        ),
        CommandType.IGNORE: CommandDefinition(
            command_type=CommandType.IGNORE,
            description="User asks for the command to be ignored.",
        ),
    }


# Command metadata for LLM matching
COMMAND_REGISTRY: dict[CommandType, CommandDefinition] = _build_registry()
