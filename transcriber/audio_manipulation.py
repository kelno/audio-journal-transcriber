from pathlib import Path
from pydub import AudioSegment, silence
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
