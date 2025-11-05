from datetime import datetime
import os
from pathlib import Path
import re

from transcriber.logger import get_logger

logger = get_logger()

def ensure_directory_exists(directory):
    """Create directory and any necessary parent directories if they don't exist."""
    if not os.path.exists(directory):
        os.makedirs(directory)

def touch_file(file_path):
    """Update file's modification time to current time"""

    os.utime(file_path, None)

def remove_empty_subdirs(directory: Path):
    """Recursively remove directories inside given directory if they're empty."""
    try:
        # Walk bottom-up so we check deepest directories first
        for root, _dirs, _files in os.walk(directory, topdown=False):
            # Skip the root directory itself
            if Path(root) == directory:
                continue

            if not os.listdir(root):  # Directory is empty (no files/subdirs)
                logger.debug(f"Removing empty directory: {root}")
                os.rmdir(root)

    except OSError as e:
        logger.warning(f"Error while removing empty directory {directory}: {e}")

# regex pattern to match obisidian recording filenames like "Recording YYYYMMDDHHMMSS"
DATE_RE_PATTERN_OBSIDIAN_RECORDING = re.compile(r'^Recording (\d{4})(\d{2})(\d{2})\d{6}')
# regex pattern to match filenames starting with "YYYY-MM-DD_", regular pattern of mine
DATE_RE_PATTERN_SPLIT = re.compile(r'^(\d{4})-(\d{2})-(\d{2})_')

def extract_date_from_recording_filename(audio_path: Path) -> datetime|None:
    """
    Try to extract a date from the audio filename.
    """
    filename = os.path.basename(audio_path)

    m = DATE_RE_PATTERN_OBSIDIAN_RECORDING.match(filename)
    if not m:
        # Try another one
        m = DATE_RE_PATTERN_SPLIT.match(filename)
        if not m:
            return None

    try:
        year = int(m.group(1))
        month = int(m.group(2))
        day = int(m.group(3))
        return datetime(year, month, day)
    except ValueError:
        logger.warning(f"Filename {filename} contains an invalid date: {m.groups()}")
        return None
