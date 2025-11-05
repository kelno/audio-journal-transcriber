
import os

def is_handled_audio_file(filename: str) -> bool:
    """
    Check if the file is an audio file based on extension.

    Args:
        filename (str): Name of the file

    Returns:
        bool: True if the file is an audio file, False otherwise
    """
    audio_extensions = ['.mp3', '.wav', '.m4a', '.flac', '.ogg', '.aac', '.mkv', '.mp4']
    return os.path.splitext(filename)[1].lower() in audio_extensions
