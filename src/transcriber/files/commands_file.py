# pyright:  reportUnknownArgumentType=false

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import override

import yaml

from transcriber.commands.command import Command
from transcriber.constants import COMMANDS_FILENAME
from transcriber.exception import InvalidCommandFileException
from transcriber.files.file_system import FileSystemService
from transcriber.files.text_file import TextFile
from transcriber.logger import logger


@dataclass
class CommandsFile(TextFile):
    """Represents gathered audio commands in the transcript."""

    commands: list[Command] = field(default_factory=list)
    file_path: Path | None = None

    def __post_init__(self) -> None:
        """Parse YAML text into commands if text is provided."""
        if self.text:
            self._parse_yaml_text()

    def _parse_yaml_text(self) -> None:
        """Parse YAML text and populate commands.

        If parsing fails, logs a warning and sets commands to empty list.

        Raises:
            InvalidCommandFileException: Unexpected data format

        """
        try:
            commands_data = yaml.safe_load(self.text)

            if commands_data is None:
                commands_data = []

            # Fallback for older version of the command format: plain string becomes commands
            if isinstance(commands_data, str):
                self.commands = [
                    Command(text=line)
                    for line in self.text.splitlines()  # Then use original text, before YAML parsing
                    if line.strip()
                ]
                return
            elif not isinstance(commands_data, list):
                raise InvalidCommandFileException(
                    command_file=self.file_path or Path("unknown"),
                    error=f"Expected top-level YAML list, got {type(commands_data).__name__}",
                )

            self.commands = [Command.from_dict(cmd) for cmd in commands_data if isinstance(cmd, dict)]
        except yaml.YAMLError:
            logger.error(
                f"Failed to parse YAML commands: {self.text}.",
            )
            raise

    @classmethod
    @override
    def get_filename(cls) -> str:
        """Get the filename for this file."""
        return COMMANDS_FILENAME

    def to_yaml(self) -> str:
        """Serialize commands to YAML format."""
        commands_data = [cmd.to_dict() for cmd in self.commands]
        return yaml.dump(
            commands_data,
            default_flow_style=False,
            sort_keys=False,
            explicit_start=True,
            allow_unicode=True,
        )

    @classmethod
    @override
    def from_file(
        cls,
        file_path: Path,
        fs_service: FileSystemService,
    ) -> "CommandsFile":
        """Create a CommandsFile instance from an existing file.

        Args:
            file_path: Path to the file to read.
            fs_service: FileSystemService instance for reading files.

        Returns:
            CommandsFile: A CommandsFile instance.

        """
        text_content = fs_service.read_file(file_path)
        return cls(text=text_content, file_path=file_path)

    @classmethod
    def from_command_list(cls, command_texts: list[str]) -> "CommandsFile":
        """Create a CommandsFile from a list of command strings.

        Args:
            command_texts: List of command text strings.

        Returns:
            CommandsFile: A CommandsFile instance with commands marked as unexecuted.

        """
        commands = [Command(text=text, executed=False) for text in command_texts]
        instance = cls(text="", commands=commands)
        return instance

    def mark_executed(self, command_index: int, tz: timezone) -> None:
        """Mark a command as executed.

        Args:
            command_index: Index of the command to mark as executed.
            tz: Timezone to use for the execution timestamp.

        """
        if 0 <= command_index < len(self.commands):
            self.commands[command_index].executed_at = datetime.now(tz)
            self.commands[command_index].executed = True

    @override
    def write(self, bundle_dir: Path, fs_service: FileSystemService) -> None:
        """Write the commands to a YAML file in the bundle directory.

        Args:
            bundle_dir: Path to the bundle directory where the file should be written.
            fs_service: FileSystemService instance for writing files.

        """
        output_file = bundle_dir / self.get_filename()
        fs_service.write_file(output_file, self.to_yaml())

    def has_commands_needing_processing(self) -> bool:
        """Return whether any command still lacks a terminal resolution."""
        return any(command.needs_processing for command in self.commands)
