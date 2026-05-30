from dataclasses import dataclass, field

from transcriber.commands.command_handlers import CommandHandler
from transcriber.commands.command_type import CommandType


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
    handler: CommandHandler
    description: str
    aliases: list[str] = field(default_factory=list)
