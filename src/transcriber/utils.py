import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from transcriber.files.file_system import FileSystemService

from .logger import logger


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

    except OSError:
        logger.exception(f"Error while removing empty directory {directory}")


def file_is_in_directory_tree(file: Path, tree: Path) -> bool:
    """Check if a file is located within a directory tree."""
    root = tree.resolve()
    file_path = file.resolve()

    is_inside = root in file_path.parents
    return is_inside


def get_file_modified_date(audio_path: Path, tz: ZoneInfo, fs_service: FileSystemService) -> datetime:
    """Get the file's date from its last modified time (format: YYYY-MM-DD).

    Falls back to current date if modification time is unavailable.
    """
    try:
        file_mtime = fs_service.get_mtime(audio_path)  # audio_path.stat().st_mtime
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
