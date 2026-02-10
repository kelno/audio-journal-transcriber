AUDIO_EXTENSIONS = [".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".mkv", ".mp4"]


def is_handled_audio_file(extension: str) -> bool:
    """
    Check if the file is an audio file based on extension.

    Args:
        extension (str): suffix, including the dot

    Returns:
        bool: True if the file is an audio file type we're managing, False otherwise
    """
    return extension.lower() in AUDIO_EXTENSIONS
