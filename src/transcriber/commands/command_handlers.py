"""Command handlers for executing command operations.

Contains the implementation logic for each command type.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from transcriber.config import TranscribeConfig
from transcriber.constants import MULTIPLE_TRANSCRIPTS_SEPARATOR, SUMMARY_FILENAME
from transcriber.exception import AbortRemainingBundleJobsException
from transcriber.files.commands_file import CommandsFile
from transcriber.files.text_file import TranscriptFile
from transcriber.logger import logger
from transcriber.transcribe_bundle import TranscribeBundle

# Returns success
CommandHandler = Callable[[TranscribeBundle, TranscribeConfig, str], None]


def command_handler(func: CommandHandler) -> CommandHandler:
    """Decorator to enforce CommandHandler interface compliance."""
    return func


@command_handler
def handle_merge(bundle: TranscribeBundle, _config: TranscribeConfig, merge_cmd_text: str) -> None:
    """Merge the current recording with the previous one.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.
        merge_cmd_text: The original command text.

    """
    # TODO: need refactor to simplify
    logger.info(f"Running merge command for {bundle} (command text: {merge_cmd_text})")

    store_dir = bundle.config.general.store_dir
    bundles = TranscribeBundle.gather_existing_bundles(
        store_dir=store_dir,
        dry_run=False,
        cleanup_bundle=False,
        config=bundle.config,
        fs_service=bundle.fs_service,
        audio_service=bundle.audio_service,
    )

    previous_bundle = TranscribeBundle.find_previous_bundle(bundle, bundles)
    if previous_bundle is None:
        msg = "No previous bundle found to merge with"
        raise ValueError(msg)

    previous_bundle_dir = previous_bundle.get_bundle_dir()
    current_bundle_dir = bundle.get_bundle_dir()

    moved_audio_paths: list[Path] = []
    for source_audio in bundle.source_audios:
        if not bundle.fs_service.file_exists(source_audio):
            msg = f"Audio file not found for merge: {source_audio}"
            raise FileNotFoundError(msg)

        target_audio = previous_bundle_dir / source_audio.name
        counter = 1
        while bundle.fs_service.file_exists(target_audio):
            target_audio = previous_bundle_dir / f"{source_audio.stem}_{counter}{source_audio.suffix}"
            counter += 1

        bundle.fs_service.move_file(source_audio, target_audio)
        moved_audio_paths.append(target_audio)

    previous_bundle.source_audios.extend(moved_audio_paths)
    previous_bundle.source_audios = TranscribeBundle.sort_audio_files_chronologically(
        previous_bundle.source_audios,
        bundle.config,
    )
    previous_bundle.metadata.original_audio_filenames = [audio.name for audio in previous_bundle.source_audios]

    audio_lengths = [
        bundle.audio_service.get_audio_duration(audio)
        for audio in previous_bundle.source_audios  # comment for format
    ]
    previous_bundle.metadata.audio_length = sum(audio_lengths)

    if bundle.commands:
        # Mark merge command as executed before merging
        bundle.set_command_executed(merge_cmd_text)
        if previous_bundle.commands:
            merged_commands = previous_bundle.commands.commands + bundle.commands.commands
            previous_bundle.commands = CommandsFile(text="", commands=merged_commands)
        else:
            previous_bundle.commands = bundle.commands

        previous_bundle.commands.write(previous_bundle_dir, bundle.fs_service)

    if previous_bundle.transcript and bundle.transcript:
        merged_text = previous_bundle.transcript.text + MULTIPLE_TRANSCRIPTS_SEPARATOR + bundle.transcript.text
        previous_bundle.transcript = TranscriptFile(merged_text)
        previous_bundle.transcript.write(previous_bundle_dir, bundle.fs_service)

    if previous_bundle.summary:
        previous_bundle.summary = None
        summary_path = previous_bundle_dir / SUMMARY_FILENAME
        if bundle.fs_service.file_exists(summary_path):
            bundle.fs_service.delete_file(summary_path)

    previous_bundle.metadata.summary_model_used = None
    previous_bundle.metadata.bundle_name_generated = False
    previous_bundle.metadata.write(previous_bundle_dir, bundle.fs_service)

    bundle.fs_service.delete_directory(current_bundle_dir)

    msg = f"Merged bundle {bundle.bundle_name} into {previous_bundle.bundle_name}"
    logger.info(msg)

    raise AbortRemainingBundleJobsException(msg)


@command_handler
def handle_delete(bundle: TranscribeBundle, _config: TranscribeConfig, cmd_text: str) -> None:
    """Delete the current recording.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.
        cmd_text: The original command text.

    """
    logger.debug(f"Running delete command for {bundle} (command text: {cmd_text})")
    bundle.set_command_executed(cmd_text)
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
