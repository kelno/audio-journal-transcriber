"""Command handlers for executing command operations.

Contains the implementation logic for each command type.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from transcriber.constants import (
    MERGE_FAILED_FILENAME,
    MULTIPLE_TRANSCRIPTS_SEPARATOR,
    SUMMARY_FILENAME,
)
from transcriber.exception import (
    AbortRemainingBundleJobsException,
    MergeBlockedException,
    NoPreviousBundleException,
    UnknownCommandException,
)
from transcriber.files.metadata import AudioFileMeta
from transcriber.files.text_file import CustomContextFile, TranscriptFile
from transcriber.logger import logger
from transcriber.transcribe_bundle import TranscribeBundle

if TYPE_CHECKING:
    from transcriber.commands.command import Command
    from transcriber.commands.command_definition import CommandHandler
    from transcriber.config import TranscribeConfig
    from transcriber.files.file_system import FileSystemService


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
        target.config,
        target.fs_service,
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


def _write_merge_failed_marker(
    bundle: TranscribeBundle,
    source_name: str,
    target_name: str,
    error: str,
    fs_service: FileSystemService,
) -> None:
    """Write an on-disk marker that a merge involving `bundle` did not complete.

    Written into both the source and the target bundle directories so a failed merge
    blocks automatic retries regardless of which bundle a later merge resolves to.
    The marker is removed on success (the source is deleted, the target marker cleared).
    """
    marker_path = bundle.get_bundle_dir() / MERGE_FAILED_FILENAME
    content = (
        f"Merge of {source_name} into {target_name} did not complete.\n"
        f"Automatic retry is BLOCKED while this marker is present. HUMAN ACTION REQUIRED: "
        f"inspect the bundle, fix any inconsistency, then delete {MERGE_FAILED_FILENAME} "
        f"to allow merging again. Error:\n"
        f"{error}\n"
    )
    fs_service.write_file(marker_path, content)


def _remove_merge_failed_marker(target: TranscribeBundle, fs_service: FileSystemService) -> None:
    """Remove the merge-failed marker once the merge has committed successfully."""
    marker_path = target.get_bundle_dir() / MERGE_FAILED_FILENAME
    if fs_service.file_exists(marker_path):
        fs_service.delete_file(marker_path)


def _merge_custom_context(source: TranscribeBundle, target: TranscribeBundle) -> None:
    """Merge custom_context.md from the source bundle into the target.

    The user-provided context of both bundles is concatenated (target's first, then
    the source's) so the combined bundle keeps the context of both recordings.
    Only the effective context is merged (markdown ``[//]:`` comment lines are
    ignored), which avoids duplicating the boilerplate default header.

    If the source has no effective context, the target is left untouched.
    """
    source_ctx = source.get_effective_context()
    if not source_ctx:
        return
    target_ctx = target.get_effective_context()
    merged = f"{target_ctx}\n\n{source_ctx}" if target_ctx else source_ctx
    target.custom_context = CustomContextFile(merged)
    target.custom_context.write(target.get_bundle_dir(), target.fs_service)


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


def _write_fail_markers(source: TranscribeBundle, target: TranscribeBundle) -> None:
    target_bundle_dir = target.get_bundle_dir()
    source_bundle_dir = source.get_bundle_dir()

    for owner, bundle_dir in (
        (target, target_bundle_dir),
        (source, source_bundle_dir),
    ):
        if owner.fs_service.file_exists(bundle_dir / MERGE_FAILED_FILENAME):
            msg = (
                f"{owner}: MERGE BLOCKED, a previous merge failed "
                f"(marker {MERGE_FAILED_FILENAME} present). Automatic retry is disabled; "
                f"HUMAN ACTION REQUIRED: inspect the bundle, fix any inconsistency, "
                f"then delete {MERGE_FAILED_FILENAME} to allow merging again."
            )
            logger.error(msg)
            raise MergeBlockedException(msg)

    # Write failure markers up front into BOTH bundles. If the merge aborts mid-way,
    # these cues stay on disk (both bundles are preserved and retryable), making a
    # partial/failed merge obvious and blocking future auto-retries from either side.
    _write_merge_failed_marker(
        bundle=source,
        source_name=source.bundle_name,
        target_name=target.bundle_name,
        error="merge interrupted before completion (source)",
        fs_service=source.fs_service,
    )
    _write_merge_failed_marker(
        bundle=target,
        source_name=source.bundle_name,
        target_name=target.bundle_name,
        error="merge interrupted before completion (target)",
        fs_service=source.fs_service,
    )


@command_handler
def handle_merge(source: TranscribeBundle, _config: TranscribeConfig, bundles_cache: set[TranscribeBundle], merge_cmd: Command) -> None:
    """Merge the current recording with the previous one.

    Args:
        source: The transcribe bundle to operate on.
        config: The transcribe configuration.
        merge_cmd: The original command triggering the merge.
        bundles_cache:

    Side effects / merge policy:
        - Audio, transcript, commands and transcript-model metadata are merged into
          the previous (target) bundle.
        - The per-bundle custom_context.md is concatenated into the target so the
          combined bundle keeps the context of both recordings.
        - The summary is always cleared (and ``summary_model_used`` reset) so it gets
          regenerated for the combined bundle. This is intentional: a summary written
          for only part of the merged content would be stale. Any summary on the
          current bundle is therefore dropped.
        - ``bundle_name_generated`` is reset to False so the name can be regenerated.
        - ``keep_forever`` is promoted to True if either source bundle had it set.
        - The current (source) bundle directory is deleted once the merge succeeds.

    """
    logger.info(f"Running merge command for {source} (command text: {merge_cmd.text})")

    target = TranscribeBundle.find_previous_bundle(source, bundles_cache)
    if target is None:
        msg = "No previous bundle found to merge with"
        raise NoPreviousBundleException(msg)

    # The merge target is chosen purely by recency within the merge window, not by
    # explicit user intent. Surface the chosen target prominently (with the time
    # gap) so a wrong-target merge is noticeable in the logs rather than silent.
    gap_hours = (source.get_bundle_date() - target.get_bundle_date()).total_seconds() / 3600
    logger.info(
        f"{source}: Merge target selected -> {target}, gap = {gap_hours:.1f}h (merge window: {source.config.general.merge_max_hours:.1f}h)",
    )

    target_bundle_dir = target.get_bundle_dir()
    source_bundle_dir = source.get_bundle_dir()

    # Circuit-breaker: a previous failed merge leaves a _merge_failed.md in BOTH the
    # source and the target. Refuse to merge automatically until a human removes the
    # relevant marker, so we never merge into a partially-mutated target nor retry a
    # source whose earlier merge failed (even if its resolved target later changes).
    _write_fail_markers(source=source, target=target)

    try:
        _merge_commands(source=source, target=target)
        _merge_transcripts(source=source, target=target)
        _merge_custom_context(source=source, target=target)

        # Clear the summary on every merge so it regenerates for the combined bundle.
        # Intentional policy: a summary produced from only part of the merged content
        # would be stale, so we always drop it (and any summary on the source bundle).
        if target.summary:
            target.summary = None
            target.metadata.summary_model_used = None
            summary_path = target_bundle_dir / SUMMARY_FILENAME
            if target.fs_service.file_exists(summary_path):
                target.fs_service.delete_file(summary_path)

        target.metadata.bundle_name_generated = False
        target.metadata.keep_forever = target.metadata.keep_forever or source.metadata.keep_forever
        # Commit target metadata (without the source audio yet) so a failure below
        # leaves the source bundle fully intact and retryable.
        target.metadata.write(target_bundle_dir, target.fs_service)

        # Move audio into the target only after the target is committed. This is the
        # last mutation before deletion, so a failed merge never strands the source
        # without its audio: the source stays whole and the merge is safely retried.
        _move_audio_to_bundle(source=source, target=target)
        # Rebuild target metadata now that the source audio is included, then commit.
        target.metadata.write(target_bundle_dir, target.fs_service)

        # The merge command now lives in the target bundle (merged above), so mark it
        # executed there. This must happen before the source is deleted, both to avoid
        # writing to a removed directory and to stop the merged command from
        # re-triggering a merge on the next run. Audio has already been moved into the
        # target, so deleting the source loses no audio.
        target.set_command_executed(merge_cmd.id)

        # Source removed only after the merge is fully committed to the target.
        source.fs_service.delete_directory(source_bundle_dir)
        bundles_cache.remove(source)
    except Exception:
        logger.exception(
            f"Merge of {source.bundle_name} into {target.bundle_name} failed; "
            f"source bundle is preserved and retryable (marker written to target).",
        )
        raise

    # Merge is now done and irreversible: clear the failure marker.
    _remove_merge_failed_marker(target, source.fs_service)

    msg = f"Merged bundle {source} into {target}"
    logger.info(msg)
    raise AbortRemainingBundleJobsException(msg)


@command_handler
def handle_delete(bundle: TranscribeBundle, _config: TranscribeConfig, bundles_cache: set[TranscribeBundle], cmd: Command) -> None:
    """Delete the current recording.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.
        cmd: The original command triggering this.
        bundles_cache:

    """
    logger.debug(f"Running delete command for {bundle} (command text: {cmd.text})")
    bundle.set_command_executed(cmd.id)
    bundle.fs_service.delete_directory(bundle.get_bundle_dir())
    bundles_cache.remove(bundle)

    msg = "Skip remaining jobs after delete command"
    raise AbortRemainingBundleJobsException(msg)


@command_handler
def handle_unknown(_bundle: TranscribeBundle, _config: TranscribeConfig, _bundles_cache: set[TranscribeBundle], cmd: Command) -> None:
    """Handle unknown command type.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.
        cmd: The original command triggering this.

    Raises:
        ValueError: Always raised as the command type is unknown.

    """
    msg = f"Unknown command type cannot be executed (command text: {cmd.text})"
    raise UnknownCommandException(msg)


@command_handler
def handle_ignore(_bundle: TranscribeBundle, _config: TranscribeConfig, _bundles_cache: set[TranscribeBundle], cmd: Command) -> None:
    """Handle ignore command type.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.
        cmd: The original command triggering this.

    """
    logger.debug(f"Command {cmd.text} is ignored.")
