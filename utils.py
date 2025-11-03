from datetime import date
import os
import logging
from pathlib import Path
import re

logger = logging.getLogger(__name__)

def ensure_directory_exists(directory):
    """Create directory and any necessary parent directories if they don't exist."""
    if not os.path.exists(directory):
        os.makedirs(directory)

def touch_file(file_path):
    """Update file's modification time to current time"""

    os.utime(file_path, None)

# regex pattern to match obisidian recording filenames like "Recording YYYYMMDDHHMMSS"
DATE_RE_PATTERN_OBSIDIAN_RECORDING = re.compile(r'^Recording (\d{4})(\d{2})(\d{2})\d{6}')
# regex pattern to match filenames starting with "YYYY-MM-DD_", regular pattern of mine
DATE_RE_PATTERN_SPLIT = re.compile(r'^\d{4}-\d{2}-\d{2}_')

def extract_date_from_recording_filename(audio_path: Path) -> date|None:
    """
    Returns "YYYY-MM-DD_(originalfilename)".
    If keep_full_original is False, the original prefix (e.g. "RecordingYYYYMMDD_") is removed
    from the part placed inside parentheses.
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
        return date(year, month, day)
    except ValueError:
        logger.warning(f"Filename {filename} contains an invalid date: {m.groups()}")
        return None
