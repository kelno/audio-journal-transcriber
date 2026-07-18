"""Command handlers for executing command operations.

Contains the implementation logic for each command type.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from transcriber.config import TranscribeConfig
from transcriber.constants import MULTIPLE_TRANSCRIPTS_SEPARATOR, SUMMARY_FILENAME
from transcriber.exception import AbortRemainingBundleJobsException, NoPreviousBundleException, UnknownCommandException
from transcriber.files.metadata import AudioFileMeta
from transcriber.files.text_file import TranscriptFile
from transcriber.logger import logger
from transcriber.transcribe_bundle import TranscribeBundle

if TYPE_CHECKING:
    from transcriber.commands.command import Command

# Returns success
CommandHandler = Callable[[TranscribeBundle, TranscribeConfig, str], None]


def command_handler(func: CommandHandler) -> CommandHandler:
    """Decorator to enforce CommandHandler interface compliance."""
    return func


def _move_audio_to_bundle(source: TranscribeBundle, target: TranscribeBundle) -> None:
    """Move audio files from source bundle to target bundle.

    Args:
        source: The source bundle containing audio files to move.
        target: The target bundle to receive the audio files.

    Side Effects:
        - Moves audio files from source to target bundle directory
        - Appends the source's per-file audio metadata (filenames + transcript models)
          to the target bundle's in-memory metadata

    Note:
        This function only mutates in-memory state (audio list + per-file metadata).
        It does NOT write metadata to disk; the caller is responsible for a single
        final metadata write once the whole merge is composed. This keeps the target
        bundle from being persisted in a half-built state.

    """
    target_bundle_dir = target.get_bundle_dir()

    moved_audio_paths: list[Path] = []
    for source_audio in source.source_audios:
        if not source.fs_service.file_exists(source_audio):
            msg = f"Audio file not found for merge: {source_audio}"
            raise FileNotFoundError(msg)

        target_audio = target_bundle_dir / source_audio.name
        counter = 1
        while source.fs_service.file_exists(target_audio):
            target_audio = target_bundle_dir / f"{source_audio.stem}_{counter}{source_audio.suffix}"
            counter += 1

        source.fs_service.move_file(source_audio, target_audio)
        moved_audio_paths.append(target_audio)

    target.source_audios.extend(moved_audio_paths)
    target.source_audios = TranscribeBundle.sort_audio_files_chronologically(
        target.source_audios,
        source.config,
    )

    # Build a mapping of final filenames to their metadata (with renamed targets).
    # This ensures metadata stays in sync with the sorted source_audios order.
    moved_names = {p.name for p in moved_audio_paths}
    source_meta_by_final_name: dict[str, AudioFileMeta] = {}
    for audio_meta in source.metadata.audio_files:
        new_filename = audio_meta.filename
        if audio_meta.filename in moved_names:
            # Find the collision-renamed target name for this source file.
            for moved in moved_audio_paths:
                if moved.stem == Path(audio_meta.filename).stem:
                    new_filename = moved.name
                    break
        source_meta_by_final_name[new_filename] = AudioFileMeta(
            filename=new_filename,
            transcript_model_used=list(audio_meta.transcript_model_used),
        )

    # Preserve existing target metadata for files that were already in target
    target_meta_by_name = {m.filename: m for m in target.metadata.audio_files}

    # Rebuild audio_files in the same order as the sorted source_audios.
    target.metadata.audio_files = [
        source_meta_by_final_name.get(path.name) or target_meta_by_name.get(path.name) or AudioFileMeta(filename=path.name)
        for path in target.source_audios
    ]


def _merge_transcripts(source: TranscribeBundle, target: TranscribeBundle) -> None:
    target_t = target.transcript
    source_t = source.transcript
    if target_t and source_t:
        merged = target_t.text + MULTIPLE_TRANSCRIPTS_SEPARATOR + source_t.text
    elif source_t and not target_t:
        merged = source_t.text  # promote current's transcript
    elif target_t and not source_t:
        merged = target_t.text  # keep previous's (no-op, but explicit)
    else:
        merged = None
    target.transcript = TranscriptFile(merged) if merged else None
    if target.transcript:
        target.transcript.write(target.get_bundle_dir(), target.fs_service)


def _merge_commands(source: TranscribeBundle, target: TranscribeBundle) -> None:
    """Merge commands from source bundle into target bundle.

    Commands are identified by their stable ``id``. If the same id appears in
    both bundles (e.g. a copy/pasted bundle directory), it is treated as the
    same command and kept only once.

    Args:
        source: The source bundle containing commands to merge.
        target: The target bundle to receive the merged commands.

    Side Effects:
        - Combines commands from both bundles into the target bundle
        - Writes the merged commands file to the target bundle directory

    """
    if target.commands and source.commands:  # merge case
        # Dedupe by id: a repeated id denotes the same command (e.g. a bundle
        # directory that was copy/pasted), so later occurrences are dropped.
        merged_by_id: dict[str, Command] = {}
        for cmd in target.commands.commands + source.commands.commands:
            merged_by_id.setdefault(cmd.id, cmd)
        target.commands.commands = list(merged_by_id.values())
    elif source.commands:  # only source has commands
        # Replace with a copy of the source commands to avoid sharing references.
        target.commands = source.commands
        target.commands.commands = list(source.commands.commands)

    if target.commands:
        target.commands.write(target.get_bundle_dir(), target.fs_service)


@command_handler
def handle_merge(current_bundle: TranscribeBundle, _config: TranscribeConfig, merge_cmd_text: str) -> None:
    """Merge the current recording with the previous one.

    Args:
        current_bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.
        merge_cmd_text: The original command text.

    Side effects / merge policy:
        - Audio, transcript, commands and transcript-model metadata are merged into
          the previous (target) bundle.
        - The summary is always cleared (and ``summary_model_used`` reset) so it gets
          regenerated for the combined bundle. This is intentional: a summary written
          for only part of the merged content would be stale. Any summary on the
          current bundle is therefore dropped.
        - ``bundle_name_generated`` is reset to False so the name can be regenerated.
        - ``keep_forever`` is promoted to True if either source bundle had it set.
        - The current (source) bundle directory is deleted once the merge succeeds.

    """
    logger.info(f"Running merge command for {current_bundle} (command text: {merge_cmd_text})")

    store_dir = current_bundle.config.general.store_dir
    bundles = TranscribeBundle.gather_existing_bundles(
        store_dir=store_dir,
        dry_run=False,
        cleanup_bundle=False,
        config=current_bundle.config,
        fs_service=current_bundle.fs_service,
        audio_service=current_bundle.audio_service,
    )

    previous_bundle = TranscribeBundle.find_previous_bundle(current_bundle, bundles)
    if previous_bundle is None:
        msg = "No previous bundle found to merge with"
        raise NoPreviousBundleException(msg)

    # The merge target is chosen purely by recency within the merge window, not by
    # explicit user intent. Surface the chosen target prominently (with the time
    # gap) so a wrong-target merge is noticeable in the logs rather than silent.
    gap_hours = (current_bundle.get_bundle_date() - previous_bundle.get_bundle_date()).total_seconds() / 3600
    logger.info(
        f"{current_bundle}: Merge target selected -> {previous_bundle}"
        f"gap = {gap_hours:.1f}h (merge window: {current_bundle.config.general.merge_max_hours:.1f}h)",
    )

    previous_bundle_dir = previous_bundle.get_bundle_dir()
    current_bundle_dir = current_bundle.get_bundle_dir()

    _move_audio_to_bundle(source=current_bundle, target=previous_bundle)
    # Mark merge command as executed before merging
    # If we don't it could trigger another merge for the target bundle
    current_bundle.set_command_executed(merge_cmd_text)
    _merge_commands(source=current_bundle, target=previous_bundle)
    _merge_transcripts(source=current_bundle, target=previous_bundle)

    # Clear the summary on every merge so it regenerates for the combined bundle.
    # Intentional policy: a summary produced from only part of the merged content
    # would be stale, so we always drop it (and any summary on the current bundle).
    if previous_bundle.summary:
        previous_bundle.summary = None
        previous_bundle.metadata.summary_model_used = None
        summary_path = previous_bundle_dir / SUMMARY_FILENAME
        if previous_bundle.fs_service.file_exists(summary_path):
            previous_bundle.fs_service.delete_file(summary_path)

    previous_bundle.metadata.bundle_name_generated = False
    previous_bundle.metadata.keep_forever = previous_bundle.metadata.keep_forever or current_bundle.metadata.keep_forever
    # Single, final metadata commit. All in-memory merge state (audio list, length,
    # transcript models, summary reset, keep_forever, bundle_name_generated) is
    # persisted here at once. The source bundle is only deleted afterwards, so a
    # failure before this point leaves the target untouched and the source recoverable.
    previous_bundle.metadata.write(previous_bundle_dir, previous_bundle.fs_service)

    current_bundle.fs_service.delete_directory(current_bundle_dir)

    msg = f"Merged bundle {current_bundle.bundle_name} into {previous_bundle.bundle_name}"
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
    raise UnknownCommandException(msg)


@command_handler
def handle_ignore(_bundle: TranscribeBundle, _config: TranscribeConfig, cmd_text: str) -> None:
    """Handle ignore command type.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.
        cmd_text: The original command text.

    """
    logger.debug(f"Command {cmd_text} is ignored.")
