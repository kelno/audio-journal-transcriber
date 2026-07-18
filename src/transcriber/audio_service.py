"""Abstract audio service for dependency injection."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import override

from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError
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
            Exception: For other unexpected errors.

        """
        ...


class RealAudioService(AudioService):
    """Real audio operations."""

    def _handle_audio_error(self, file_path: Path, exception: Exception) -> None:
        """Handle audio processing errors by logging and re-raising.

        Args:
            file_path: Path to the audio file that caused the error.
            exception: The exception that was raised.

        Raises:
            Exception: Re-raises the original exception after logging.

        """
        if isinstance(exception, FileNotFoundError):
            logger.error(f"File not found: {file_path}")
        elif isinstance(exception, CouldntDecodeError):
            logger.error(f"Unsupported or corrupted audio file: {file_path}")
        else:
            logger.error(
                f"Unexpected error {type(exception).__name__}: reading {file_path}",
            )
        raise exception

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
        """Return audio duration in seconds using pydub."""
        try:
            audio: AudioSegment = AudioSegment.from_file(str(file_path))
            duration: float = audio.duration_seconds

            if duration <= 0.0:
                msg = "Audio duration is zero or negative"
                raise ValueError(msg)  # noqa: TRY301

            return duration

        except FileNotFoundError as e:
            self._handle_audio_error(file_path, e)
        except CouldntDecodeError as e:
            self._handle_audio_error(file_path, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self._handle_audio_error(file_path, e)

        # This line should never be reached, but satisfies the type checker.
        msg = "Unexpected execution path in get_audio_duration"
        raise RuntimeError(msg)
