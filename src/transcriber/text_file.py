from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import override

from transcriber.constants import SUMMARY_FILENAME, TRANSCRIPT_FILENAME


@dataclass
class TextFile:
    text: str

    @abstractmethod
    def get_filename(self) -> str:
        pass

    def write(self, bundle_dir: Path):
        output_file = bundle_dir / self.get_filename()
        output_file.write_text(self.text, encoding="utf-8")

    @classmethod
    def from_file(cls, file_path: Path):
        text = file_path.read_text(encoding="utf-8")
        return cls(text=text)


@dataclass
class SummaryFile(TextFile):
    @override
    def get_filename(self) -> str:
        return SUMMARY_FILENAME


@dataclass
class TranscriptFile(TextFile):
    @override
    def get_filename(self) -> str:
        return TRANSCRIPT_FILENAME
