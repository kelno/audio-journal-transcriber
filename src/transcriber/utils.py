import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .logger import logger


def ensure_directory_exists(directory: Path) -> None:
    """Create directory and any necessary parent directories if they don't exist."""
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)


def remove_empty_subdirs(directory: Path) -> None:
    """Recursively remove directories inside given directory if they're empty."""
    try:
        # Walk bottom-up so we check deepest directories first
        for root, _dirs, _files in os.walk(directory, topdown=False):
            # Skip the root directory itself
            root_path = Path(root)
            if root_path == directory:
                continue

            if not root_path.iterdir():  # Directory is empty (no files/subdirs)
                logger.debug(f"Removing empty directory: {root}")
                root_path.rmdir()

    except OSError as e:
        logger.warning(f"Error while removing empty directory {directory}: {e}")


# regex pattern to match obisidian recording filenames like "Recording YYYYMMDDHHMMSS"
DATE_RE_PATTERN_OBSIDIAN_RECORDING = re.compile(
    r"^Recording (\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})",
)
# regex pattern to match filenames starting with "YYYY-MM-DD_", regular pattern of mine
DATE_RE_PATTERN_SPLIT = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[_ ]")


def extract_date_from_recording_filename(
    filename: str,
    tz: ZoneInfo,
) -> datetime | None:
    """Try to extract a date from the audio filename."""
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
        hour = int(m.group(4)) if m.lastindex >= 4 else 0
        minute = int(m.group(5)) if m.lastindex >= 5 else 0
        second = int(m.group(6)) if m.lastindex >= 6 else 0
        return datetime(year, month, day, hour, minute, second, tzinfo=tz)
    except ValueError:
        logger.warning(f"Filename {filename} contains an invalid date: {m.groups()}")
        return None


def file_is_in_directory_tree(file: Path, tree: Path) -> bool:
    """Check if a file is located within a directory tree."""
    root = tree.resolve()
    file_path = file.resolve()

    is_inside = root in file_path.parents
    return is_inside


def get_file_modified_date(audio_path: Path, tz: ZoneInfo) -> datetime:
    """Get the file's date from its last modified time (format: YYYY-MM-DD).

    Falls back to current date if modification time is unavailable.
    """
    try:
        file_mtime = audio_path.stat().st_mtime
        file_date = datetime.fromtimestamp(file_mtime, tz=tz)
        # logger.debug(f"Using file last modified date: '{file_date}'")
        return file_date
    except OSError:
        file_date = datetime.now(tz=tz)
        logger.warning(
            f"Could not get file modification time, using current date: '{file_date}'",
        )
        return file_date


def get_days_since_time(time: datetime) -> int:
    """Get number of days since given time.

    Args:
        time: The datetime to compare against current time.

    Returns:
        int: Number of days since the given time.

    """
    now = datetime.now(tz=time.tzinfo)
    passed = (now - time).days
    return passed
