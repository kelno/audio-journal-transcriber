"""Command handlers for executing command operations.

Contains the implementation logic for each command type.
"""

from __future__ import annotations

from collections.abc import Callable

from transcriber.config import TranscribeConfig
from transcriber.exception import AbortRemainingBundleJobsException
from transcriber.logger import logger
from transcriber.transcribe_bundle import TranscribeBundle

# Returns success
CommandHandler = Callable[[TranscribeBundle, TranscribeConfig, str], None]


def command_handler(func: CommandHandler) -> CommandHandler:
    """Decorator to enforce CommandHandler interface compliance."""
    return func


@command_handler
def handle_merge(_bundle: TranscribeBundle, _config: TranscribeConfig, cmd_text: str) -> None:
    """Merge the current recording with the previous one.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.
        cmd_text: The original command text.

    """
    # TODO: Implement merge logic https://github.com/kelno/audio-journal-transcriber/issues/3
    # So basically the use case looks like this:
    # User wants to merge the current recording with the previous one, and have it considered as one by the transcription system here
    # For example user gets interrupted or resumes later for any reason
    #
    # There might be more than 2 records
    # On the tech side I'm sure about the whole process but here are bits of what I imagine:
    # Our transcription system needs to support more than one record per bundle (partly (or fully?) implemented)
    # To know there is a merge, the record already needs to be processed into a bundle once to have the commands listed
    # Only then when processing commands here we actually merge this bundle

    # I imagine the process is basically :
        # - Find the previous bundle (by date). With a configurable maximum merge time? Like 12h by default
        # - Add the audio to previous record (move file and add to metadata of previous bundle)
        # - Merge commands with previous file
        # - Append transcript to the one of previous file
        # - Clear the summary of previous bundle, as it will need to be regenerated with the new audio.
        # - Also mark as name not generated to trigger a new name generation.
        # - Nuke current bundle
        # - Try to make that all atomic as much as possible, avoiding to have a partially merged record. We need to think how to leave things in a workable state if that happens.
        # - We'll also need to make sure to remove remaining jobs for this bundle somehow, might need some refactoring too. Maybe using exception.
        #    - If there are more unprocessed command... Well let's just say they won't be processed in this loop. Same thing for regenerating the summary & name generation.
    # If more than one record is to be merged into the same bundle (example 3 records following each other), I think we're fine as long as we process bundles in chronological order. Maybe add a comment on the process loop explaining this is a hard requirement for merge to work.

    msg = "Merge command handler not yet implemented"
    raise NotImplementedError(msg)

    # raise AbortRemainingJobsException("Skip remaining jobs after merge command")


@command_handler
def handle_delete(bundle: TranscribeBundle, _config: TranscribeConfig, cmd_text: str) -> None:
    """Delete the current recording.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.
        cmd_text: The original command text.

    """
    logger.info(f"Running delete command for {bundle} (command text: {cmd_text})")
    bundle.fs_service.delete_directory(bundle.get_bundle_dir())

    msg = "Skip remaining jobs after delete command"
    raise AbortRemainingBundleJobsException(msg)


@command_handler
def handle_unknown(_bundle: TranscribeBundle, _config: TranscribeConfig, cmd_text: str) -> None:
    """Handle unknown command type.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.
        cmd_text: The original command text.

    Raises:
        ValueError: Always raised as the command type is unknown.

    """
    msg = f"Unknown command type cannot be executed (command text: {cmd_text})"
    raise ValueError(msg)


@command_handler
def handle_ignore(_bundle: TranscribeBundle, _config: TranscribeConfig, cmd_text: str) -> None:
    """Handle ignore command type.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.
        cmd_text: The original command text.

    """
    logger.debug(f"Command {cmd_text} is ignored.")
