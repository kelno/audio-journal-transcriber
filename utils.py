import os


def ensure_directory_exists(directory):
    """Create directory and any necessary parent directories if they don't exist."""
    if not os.path.exists(directory):
        os.makedirs(directory)

def touch_file(file_path):
    """Update file's modification time to current time"""

    os.utime(file_path, None)
