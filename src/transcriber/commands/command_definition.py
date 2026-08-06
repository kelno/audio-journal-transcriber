from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from transcriber.commands.command_execution_policy import CommandExecutionPolicy

if TYPE_CHECKING:
    from transcriber.commands.command import Command
    from transcriber.commands.command_type import CommandType
    from transcriber.config import TranscribeConfig
    from transcriber.transcribe_bundle import BundleCache, TranscribeBundle

# Signature shared by every command handler: (bundle, config, command_text) -> None.
# Defined here rather than in command_handlers to avoid a circular import cycle.
CommandHandler = Callable[["TranscribeBundle", "TranscribeConfig", "BundleCache", "Command"], None]


@dataclass
class CommandDefinition:
    """Metadata for a command type to aid in LLM matching.

    Attributes:
        command_type: The type of command to execute.
        handler: Function for command types executed directly. Action-backed
            command types leave this unset and are dispatched by the action layer.
        description: Description for LLM to understand the command's purpose.
        max_attempts: How many times the command can be attempted before being considered failed.
        aliases: Optional list of alternative names for this command.
        execution_policy: How repeated commands of this type are selected.
        argument_instructions: Description of the JSON arguments expected from
            command interpretation.

    """

    command_type: CommandType
    handler: CommandHandler | None
    description: str
    max_attempts: int
    aliases: list[str] = field(default_factory=list)
    execution_policy: CommandExecutionPolicy = CommandExecutionPolicy.ONCE_PER_BUNDLE
    argument_instructions: str = "Use an empty object."
