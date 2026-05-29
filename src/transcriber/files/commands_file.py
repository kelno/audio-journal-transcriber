# pyright:  reportUnknownArgumentType=false

from dataclasses import dataclass
from pathlib import Path
from typing import override

from transcriber.constants import COMMANDS_FILENAME
from transcriber.files.file_system import FileSystemService
from transcriber.files.text_file import TextFile


@dataclass
class CommandsFile(TextFile):
    """Represents gathered audio commands in the transcript."""

    @override
    def get_filename(self) -> str:
        return COMMANDS_FILENAME

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
        text = fs_service.read_file(file_path)
        return cls(text=text)
