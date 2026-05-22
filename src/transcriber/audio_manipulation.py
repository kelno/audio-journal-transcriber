from pathlib import Path

from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError
from pydub.utils import which as pydub_which

from transcriber.logger import logger


class AudioManipulation:
    @staticmethod
    def validate_ffmpeg() -> bool:
        """Validate that ffmpeg is available in the system path.

        Returns:
            bool: True if ffmpeg is found, False otherwise.

        """
        ffmpeg_path = pydub_which("ffmpeg")
        if not ffmpeg_path:
            logger.error("ffmpeg was not found in path")
            return False

        logger.debug(f"ffmpeg found at {ffmpeg_path}")
        return True

    @staticmethod
    def _handle_audio_error(file_path: Path, exception: Exception) -> None:
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
            logger.error(f"Unexpected error reading {file_path}")
        raise exception

    @staticmethod
    def get_audio_duration(file_path: Path) -> float:
        """Return audio duration in seconds using pydub.

        Args:
            file_path: Path to the audio file.

        Returns:
            float: Audio duration in seconds.

        Raises:
            FileNotFoundError: If the file does not exist.
            CouldntDecodeError: If the audio file is unsupported or corrupted.
            Exception: For other unexpected errors.

        """
        try:
            audio = AudioSegment.from_file(str(file_path))
            duration = audio.duration_seconds
            if duration <= 0:
                error_msg = "Audio duration is zero or negative"
                raise ValueError(error_msg)  # noqa: TRY301

            return duration
        except FileNotFoundError as e:
            AudioManipulation._handle_audio_error(file_path, e)
        except CouldntDecodeError as e:
            AudioManipulation._handle_audio_error(file_path, e)
        except Exception as e:  # pylint: disable=broad-exception-caught # noqa: BLE001
            AudioManipulation._handle_audio_error(file_path, e)

        # This line should never be reached, but satisfies the linter
        error_msg = "Unexpected execution path in get_audio_duration"
        raise RuntimeError(error_msg)
