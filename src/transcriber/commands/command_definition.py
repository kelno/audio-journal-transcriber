from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from transcriber.commands.command_execution_policy import CommandExecutionPolicy

if TYPE_CHECKING:
    from transcriber.commands.command_type import CommandType


@dataclass
class CommandDefinition:
    """Metadata for a command type to aid in LLM matching.

    Attributes:
        command_type: The type of command to execute.
        description: Description for LLM to understand the command's purpose.
        aliases: Optional list of alternative names for this command.
        execution_policy: How repeated commands of this type are selected.
        argument_instructions: Description of the JSON arguments expected from
            command interpretation.

    """

    command_type: CommandType
    description: str
    aliases: list[str] = field(default_factory=list)
    execution_policy: CommandExecutionPolicy = CommandExecutionPolicy.ONCE_PER_BUNDLE
    argument_instructions: str = "Use an empty object."
