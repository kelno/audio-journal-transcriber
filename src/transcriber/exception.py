from __future__ import annotations

from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from pathlib import Path


class AudioTranscriberException(Exception):
    """Base exception for audio transcriber errors."""


@final
class TooShortException(AudioTranscriberException):
    """Audio length is too short to process."""

    def __init__(self, source_audio: Path, audio_length: float, min_length: float, context: str | None = None):
        message = f"Audio file too short ({audio_length} < {min_length} seconds)"
        if context:
            message = f"{message}: {context}"
        super().__init__(message)
        self.source_audio = source_audio
        self.audio_length = audio_length
        self.min_length = min_length


@final
class EmptyTranscriptException(AudioTranscriberException):
    """Transcript is empty after transcription."""

    def __init__(self, source_audio: Path, context: str | None = None):
        message = f"Transcript is empty for audio file: {source_audio}"
        if context:
            message = f"{message}: {context}"
        super().__init__(message)
        self.source_audio = source_audio


@final
class AbortRemainingBundleJobsException(AudioTranscriberException):
    """Abort remaining jobs in the queue for current bundle. This is not an error case."""


@final
class InvalidBundleException(AudioTranscriberException):
    """Failed to load invalid bundle."""

    def __init__(self, bundle_path: Path, reason: str, context: str | None = None):
        message = f"Invalid bundle at {bundle_path}: {reason}"
        if context:
            message = f"{message}: {context}"
        super().__init__(message)
        self.bundle_path = bundle_path
        self.reason = reason


@final
class DuplicateBundleIdException(AudioTranscriberException):
    """Two stored bundle directories claim the same persistent identity."""

    def __init__(self, bundle_id: str, first_path: Path, second_path: Path):
        """Describe the conflicting identifier and both bundle directories.

        Args:
            bundle_id: Persistent identifier claimed by both bundles.
            first_path: Directory of the first bundle encountered.
            second_path: Directory of the conflicting bundle.

        """
        super().__init__(f"Duplicate bundle_id {bundle_id}: {first_path} and {second_path}")
        self.bundle_id = bundle_id
        self.first_path = first_path
        self.second_path = second_path


@final
class NoPreviousBundleException(AudioTranscriberException):
    """Failed to find a previous bundle for merge command."""

    def __init__(self, target_bundle: str, search_pattern: str, context: str | None = None):
        message = f"No previous bundle found for {target_bundle} using pattern: {search_pattern}"
        if context:
            message = f"{message}: {context}"
        super().__init__(message)
        self.target_bundle = target_bundle
        self.search_pattern = search_pattern


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
class InvalidBundleTitleException(AudioTranscriberException):
    """A proposed bundle title cannot produce a valid directory name."""

    def __init__(self, invalid_title: str, reason: str, context: str | None = None):
        message = f"Invalid bundle title '{invalid_title}': {reason}"
        if context:
            message = f"{message}: {context}"
        super().__init__(message)
        self.invalid_title = invalid_title
        self.reason = reason


@final
class UnexpectedChatClientAnswerException(AudioTranscriberException):
    """Chat client didn't return expected answer."""

    def __init__(self, expected_type: str, actual_answer: str, context: str | None = None):
        message = f"Expected {expected_type} but got: {actual_answer}"
        if context:
            message = f"{message}: {context}"
        super().__init__(message)
        self.expected_type = expected_type
        self.actual_answer = actual_answer


@final
class InvalidMetadataFileException(AudioTranscriberException):
    """Failed to create metadata from invalid file."""

    def __init__(self, metadata_file: Path, error: str, context: str | None = None):
        message = f"Invalid metadata file {metadata_file}: {error}"
        if context:
            message = f"{message}: {context}"
        super().__init__(message)
        self.metadata_file = metadata_file
        self.error = error


@final
class InvalidSourceAudiosException(AudioTranscriberException):
    """Failed to create bundle because of invalid source audios."""

    def __init__(self, source_files: list[Path], reason: str, context: str | None = None):
        message = f"Invalid source audios ({len(source_files)} files): {reason}"
        if context:
            message = f"{message}: {context}"
        super().__init__(message)
        self.source_files = source_files
        self.reason = reason


@final
class FailedToExtractDateException(AudioTranscriberException):
    """Failed to get a date from file names."""

    def __init__(self, filenames: list[str], error: str, context: str | None = None):
        message = f"Failed to extract date from filenames: {error}"
        if context:
            message = f"{message}: {context}"
        super().__init__(message)
        self.filenames = filenames
        self.error = error


@final
class InvalidCommandFileException(AudioTranscriberException):
    """Failed to process command file."""

    def __init__(self, command_file: Path, error: str, context: str | None = None):
        message = f"Invalid command file {command_file}: {error}"
        if context:
            message = f"{message}: {context}"
        super().__init__(message)
        self.command_file = command_file
        self.error = error
