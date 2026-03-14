from pathlib import Path


class AudioTranscriberException(Exception):
    """Base exception for audio transcriber errors."""


class TooShortException(AudioTranscriberException):
    """Audio length is too short to process."""

    def __init__(self, source_audio: Path, audio_length: float, min_length: float):
        message = f"Audio file too short ({audio_length} < {min_length} seconds)"
        super().__init__(message)
        self.source_audio = source_audio


class EmptyTranscriptException(AudioTranscriberException):
    """Transcript is empty after transcription."""
