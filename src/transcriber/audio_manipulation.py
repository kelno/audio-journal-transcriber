from pathlib import Path
from pydub import AudioSegment
from pydub.utils import which as pydub_which
from pydub.exceptions import CouldntDecodeError

from transcriber.logger import get_logger

logger = get_logger()


class AudioManipulation:
    @staticmethod
    def validate_ffmpeg() -> bool:
        ffmpeg_path = pydub_which("ffmpeg")
        if not ffmpeg_path:
            logger.error("ffmpeg was not found in path")
            return False
        else:
            logger.debug(f"ffmpeg found at {ffmpeg_path}")
            return True

    @staticmethod
    def get_audio_duration(file_path: Path) -> float:
        """Return audio duration in seconds using pydub."""
        try:
            audio = AudioSegment.from_file(str(file_path))
            duration = audio.duration_seconds
            if duration <= 0:
                raise ValueError("Audio duration is zero or negative")

            return duration
        except FileNotFoundError:
            logger.error(f"File not found: {file_path}")
            raise
        except CouldntDecodeError:
            logger.error(f"Unsupported or corrupted audio file: {file_path}")
            raise
        except Exception:  # pylint: disable=broad-exception-caught
            logger.error(f"Unexpected error reading {file_path}")
            raise
