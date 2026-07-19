"""Abstract audio service for dependency injection."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import override

from pydub import AudioSegment
from pydub.utils import which as pydub_which

from transcriber.logger import logger


class AudioService(ABC):
    """Abstract interface for audio operations."""

    @abstractmethod
    def validate_ffmpeg(self) -> bool:
        """Validate that ffmpeg is available in the system path.

        Returns:
            bool: True if ffmpeg is found, False otherwise.

        """
        ...

    @abstractmethod
    def get_audio_duration(self, file_path: Path) -> float:
        """Return audio duration in seconds.

        Args:
            file_path: Path to the audio file.

        Returns:
            float: Audio duration in seconds.

        Raises:
            FileNotFoundError: If the file does not exist.
            CouldntDecodeError: If the audio file is unsupported or corrupted.
            ValueError: If the decoded audio duration is zero or negative.
            Exception: For other unexpected errors from the underlying decoder.

        """
        ...


class RealAudioService(AudioService):
    """Real audio operations."""

    @override
    def validate_ffmpeg(self) -> bool:
        """Validate that ffmpeg is available in the system path."""
        ffmpeg_path = pydub_which("ffmpeg")
        if not ffmpeg_path:
            logger.error("ffmpeg was not found in path")
            return False

        logger.debug(f"ffmpeg found at {ffmpeg_path}")
        return True

    @override
    def get_audio_duration(self, file_path: Path) -> float:
        """Return audio duration in seconds using pydub.

        The underlying decoder (pydub/ffmpeg) raises on missing, unsupported, or
        corrupted files; those exceptions propagate to the caller, which is
        responsible for logging the affected file path.
        """
        audio: AudioSegment = AudioSegment.from_file(str(file_path))
        duration: float = audio.duration_seconds

        if duration <= 0.0:
            msg = "Audio duration is zero or negative"
            raise ValueError(msg)

        return duration
