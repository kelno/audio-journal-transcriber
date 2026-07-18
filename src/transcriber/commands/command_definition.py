from dataclasses import dataclass, field

from transcriber.commands.command_handlers import CommandHandler
from transcriber.commands.command_type import CommandType


@dataclass
class CommandDefinition:
    """Metadata for a command type to aid in LLM matching.

    Attributes:
        command_type: The type of command to execute.
        handler: Function to execute the command. Takes bundle and config.
        description: Description for LLM to understand the command's purpose.
        max_attempts: How many times the command can be attempted before being considered failed.
        aliases: Optional list of alternative names for this command.

    """

    command_type: CommandType
    handler: CommandHandler
    description: str
    max_attempts: int
    aliases: list[str] = field(default_factory=list)
