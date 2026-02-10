class AudioTranscriberException(Exception):
    """Base exception for audio transcriber errors."""


class TooShortException(AudioTranscriberException):
    """Audio length is too short to process."""


class EmptyTranscriptException(AudioTranscriberException):
    """Transcript is empty after transcription."""
