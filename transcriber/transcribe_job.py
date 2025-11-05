
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TranscribeJob:
    audio_file: Path
