from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import override

from transcriber.constants import SUMMARY_FILENAME, TRANSCRIPT_FILENAME


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

    def write(self, bundle_dir: Path) -> None:
        """Write the text content to a file in the bundle directory.

        Args:
            bundle_dir: Path to the bundle directory where the file should be written.

        """
        output_file = bundle_dir / self.get_filename()
        output_file.write_text(self.text, encoding="utf-8")

    def unlink(self, bundle_dir: Path) -> None:
        """Delete the text file from the bundle directory.

        Args:
            bundle_dir: Path to the bundle directory containing the file to delete.

        """
        output_file = bundle_dir / self.get_filename()
        output_file.unlink()

    @classmethod
    def from_file(cls, file_path: Path) -> "TextFile":
        """Create a TextFile instance from an existing file.

        Args:
            file_path: Path to the file to read.

        Returns:
            TextFile: An instance of the appropriate TextFile subclass.

        """
        text = file_path.read_text(encoding="utf-8")
        return cls(text=text)


@dataclass
class SummaryFile(TextFile):
    """Represents a summary file in a bundle."""

    @override
    def get_filename(self) -> str:
        return SUMMARY_FILENAME

    @classmethod
    @override
    def from_file(cls, file_path: Path) -> "SummaryFile":
        """Create a SummaryFile instance from an existing file.

        Args:
            file_path: Path to the file to read.

        Returns:
            SummaryFile: A SummaryFile instance.

        """
        text = file_path.read_text(encoding="utf-8")
        return cls(text=text)


@dataclass
class TranscriptFile(TextFile):
    """Represents a transcript file in a bundle."""

    @override
    def get_filename(self) -> str:
        return TRANSCRIPT_FILENAME

    @classmethod
    @override
    def from_file(cls, file_path: Path) -> "TranscriptFile":
        """Create a TranscriptFile instance from an existing file.

        Args:
            file_path: Path to the file to read.

        Returns:
            TranscriptFile: A TranscriptFile instance.

        """
        text = file_path.read_text(encoding="utf-8")
        return cls(text=text)
