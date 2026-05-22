from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import override

from transcriber.constants import (
    COMMANDS_FILENAME,
    SUMMARY_FILENAME,
    TRANSCRIPT_FILENAME,
)
from transcriber.file_system import FileSystemService


@dataclass
class TextFile:
    """Base class for text files in a bundle."""

    text: str

    @abstractmethod
    def get_filename(self) -> str:
        """Get the filename for this text file.

        Returns:
            str: The filename.

        """
        ...

    def write(self, bundle_dir: Path, fs_service: FileSystemService) -> None:
        """Write the text content to a file in the bundle directory.

        Args:
            bundle_dir: Path to the bundle directory where the file should be written.
            fs_service: FileSystemService instance for writing files.

        """
        output_file = bundle_dir / self.get_filename()
        fs_service.write_file(output_file, self.text)

    def unlink(self, bundle_dir: Path, fs_service: FileSystemService) -> None:
        """Delete the text file from the bundle directory.

        Args:
            bundle_dir: Path to the bundle directory containing the file to delete.
            fs_service: FileSystemService instance for deleting files.

        """
        output_file = bundle_dir / self.get_filename()
        fs_service.delete_file(output_file)

    @classmethod
    def from_file(cls, file_path: Path, fs_service: FileSystemService) -> "TextFile":
        """Create a TextFile instance from an existing file.

        Args:
            file_path: Path to the file to read.
            fs_service: FileSystemService instance for reading files.

        Returns:
            TextFile: An instance of the appropriate TextFile subclass.

        """
        text = fs_service.read_file(file_path)
        return cls(text=text)


@dataclass
class SummaryFile(TextFile):
    """Represents a summary file in a bundle."""

    @override
    def get_filename(self) -> str:
        return SUMMARY_FILENAME

    @classmethod
    @override
    def from_file(
        cls,
        file_path: Path,
        fs_service: FileSystemService,
    ) -> "SummaryFile":
        """Create a SummaryFile instance from an existing file.

        Args:
            file_path: Path to the file to read.
            fs_service: FileSystemService instance for reading files.

        Returns:
            SummaryFile: A SummaryFile instance.

        """
        text = fs_service.read_file(file_path)
        return cls(text=text)


@dataclass
class TranscriptFile(TextFile):
    """Represents a transcript file in a bundle."""

    @override
    def get_filename(self) -> str:
        return TRANSCRIPT_FILENAME

    @classmethod
    @override
    def from_file(
        cls,
        file_path: Path,
        fs_service: FileSystemService,
    ) -> "TranscriptFile":
        """Create a TranscriptFile instance from an existing file.

        Args:
            file_path: Path to the file to read.
            fs_service: FileSystemService instance for reading files.

        Returns:
            TranscriptFile: A TranscriptFile instance.

        """
        text = fs_service.read_file(file_path)
        return cls(text=text)


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
