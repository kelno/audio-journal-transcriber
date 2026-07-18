"""Fake audio manipulation service for testing."""

from pathlib import Path
from typing import override

import pytest

from transcriber.audio_service import AudioService


class FakeAudioService(AudioService):
    """Fake audio manipulation for testing without actual file I/O."""

    def __init__(self) -> None:
        """Initialize the fake audio manipulation."""
        super().__init__()
        # Store durations by full path
        self.audio_durations: dict[Path, float] = {}
        # Also keep track by filename for lookups after file moves
        self.durations_by_filename: dict[str, float] = {}

    def set_audio_duration(self, path: Path, duration: float) -> None:
        """Set the duration for a fake audio file.

        Args:
            path: Path to the audio file.
            duration: Duration in seconds.

        """
        self.audio_durations[path] = duration
        self.durations_by_filename[path.name] = duration

    @override
    def validate_ffmpeg(self) -> bool:
        return True

    @override
    def get_audio_duration(self, file_path: Path) -> float:
        """Return preset audio duration.

        Looks up duration by full path first, then by filename if path not found.
        This handles cases where files are moved to different directories during operations.

        Args:
            file_path: Path to the audio file.

        Returns:
            float: Audio duration in seconds from preset values.

        Raises:
            KeyError: If the audio file duration was not preset.

        """
        # First try exact path match
        if file_path in self.audio_durations:
            return self.audio_durations[file_path]

        # Fall back to filename match (handles file moves)
        if file_path.name in self.durations_by_filename:
            return self.durations_by_filename[file_path.name]

        msg = f"Audio duration not set for {file_path}. Set it with set_audio_duration()"
        raise KeyError(msg)


@pytest.fixture
def fake_audio_service() -> FakeAudioService:
    """Create a fresh fake file system for each test."""
    return FakeAudioService()
