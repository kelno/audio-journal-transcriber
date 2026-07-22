from __future__ import annotations

from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from pathlib import Path

    from transcriber.transcribe_bundle import TranscribeBundle


class AudioTranscriberException(Exception):
    """Base exception for audio transcriber errors."""


@final
class TooShortException(AudioTranscriberException):
    """Audio length is too short to process."""

    def __init__(self, source_audio: Path, audio_length: float, min_length: float):
        message = f"Audio file too short ({audio_length} < {min_length} seconds)"
        super().__init__(message)
        self.source_audio = source_audio


@final
class EmptyTranscriptException(AudioTranscriberException):
    """Transcript is empty after transcription."""


@final
class AbortRemainingBundleJobsException(AudioTranscriberException):
    """Abort remaining jobs in the queue for current bundle. This is not an error case."""


@final
class InvalidBundleException(AudioTranscriberException):
    """Failed to load invalid bundle."""


@final
class NoPreviousBundleException(AudioTranscriberException):
    """Failed to find a previous bundle for merge command."""


@final
class AbortForMergeTargetException(AudioTranscriberException):
    """Abort remaining jobs for the current (source) bundle and re-process `bundle`.

    Raised by a merge: the source bundle is deleted, so its remaining jobs must be
    skipped, while the merge target's summary/name were invalidated and must be
    regenerated within the same run. `bundle` is the (already merged and committed)
    target bundle to re-gather and re-process.
    """

    def __init__(self, bundle: TranscribeBundle, message: str):
        super().__init__(message)
        self.bundle = bundle


@final
class MergeBlockedException(AudioTranscriberException):
    """A previous merge into the target bundle failed and left a failure marker.

    Auto-retry is blocked until the marker is removed by hand, so a partially
    merged/inconsistent target is not merged into again automatically.
    """


@final
class UnknownCommandException(AudioTranscriberException):
    """Command text couldn't be matched to a know command type."""


@final
class EmptyChatClientAnswerException(AudioTranscriberException):
    """Chat client returned an empty answer."""


@final
class InvalidBundleNameAnswerException(AudioTranscriberException):
    """Chat client returned an invalid bundle name."""


@final
class UnexpectedChatClientAnswerException(AudioTranscriberException):
    """Chat client didn't return expected answer."""


@final
class InvalidMetadataFileException(AudioTranscriberException):
    """Failed to create metadata from invalid file."""


@final
class InvalidSourceAudiosException(AudioTranscriberException):
    """Failed to create bundle because of invalid source audios."""


@final
class FailedToExtractDateException(AudioTranscriberException):
    """Failed to get a date from file names."""


@final
class InvalidCommandFileException(AudioTranscriberException):
    """Failed to get a date from file names."""
