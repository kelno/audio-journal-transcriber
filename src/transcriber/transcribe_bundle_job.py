import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import override

from transcriber.actions.action import Action, DeleteAction, MergeAction, PreviousBundleTarget, SetTitleAction
from transcriber.actions.action_request import (
    ActionBlocked,
    ActionEffects,
    ActionFailed,
    ActionRequest,
    ActionResult,
    ActionSucceeded,
    CommandActionOrigin,
    new_action_request_id,
)
from transcriber.actions.action_runtime import ActionRuntime
from transcriber.actions.action_service import ProcessedActionRequest
from transcriber.bundle_title import BundleTitleState
from transcriber.commands.command import Command
from transcriber.commands.command_execution_policy import CommandExecutionPolicy
from transcriber.commands.command_interpretation import SetTitleCommandArguments
from transcriber.commands.command_registry import COMMAND_REGISTRY
from transcriber.commands.command_resolution import ActionRequestCommandResolution
from transcriber.commands.command_type import CommandType
from transcriber.constants import (
    MULTIPLE_TRANSCRIPTS_SEPARATOR,
)

from .ai_manager import AIManager
from .config import TranscribeConfig
from .exception import (
    AbortRemainingBundleJobsException,
    EmptyTranscriptException,
    MergeBlockedException,
    TooShortException,
)
from .files.metadata import AudioFileMeta
from .logger import logger
from .transcribe_bundle import BundleCache, TranscribeBundle


@dataclass
class TranscribeBundleJob(ABC):
    """Abstract base class for all job types."""

    bundle: TranscribeBundle
    dry_run: bool

    @abstractmethod
    def run(self, ai_manager: AIManager, config: TranscribeConfig, bundle_cache: BundleCache) -> ActionEffects | None:
        """Perform the job's main work.

        Args:
            ai_manager: AI operations available to the job.
            config: Application configuration for the current run.
            bundle_cache: Loaded bundles indexed by persistent ID.

        """

    @override
    def __str__(self) -> str:
        """Return a string representation of the job."""
        return f"[{self.__class__.__name__}:{self.bundle.bundle_name}]"


# Job list for a single bundle
type BundleJobs = list[TranscribeBundleJob]


@dataclass
class CreateBundleJob(TranscribeBundleJob):
    """Move all audio files into the bundle directory."""

    @override
    def run(
        self,
        ai_manager: AIManager,
        config: TranscribeConfig,
        bundle_cache: BundleCache,
    ) -> None:
        """Move all audio files into the bundle directory.

        Args:
            ai_manager: AI operations supplied through the shared job interface.
            config: Configuration controlling audio validation and storage.
            bundle_cache: Loaded bundles indexed by persistent ID.

        Raises:
            FileNotFoundError: If bundle has no audio files set.

        """
        if not self.bundle.source_audios:
            error_msg = "Bundle has no audio files set"
            raise FileNotFoundError(error_msg)

        self.bundle.ensure_unique_bundle_name()

        final_audio_paths = self.bundle.get_bundle_audio_paths()
        files_to_move: list[tuple[Path, Path]] = []

        # Check all files and validate
        for source_path, final_path in zip(
            self.bundle.source_audios,
            final_audio_paths,
            strict=True,
        ):
            if source_path != final_path:
                files_to_move.append((source_path, final_path))

        if files_to_move:
            logger.info(f"Moving {len(files_to_move)} audio file(s) into bundle")

            if not self.dry_run:
                self._validate_audio_files(files_to_move, config.general.min_length_seconds)

                # Move all files
                self.bundle.fs_service.ensure_directory_exists(final_audio_paths[0].parent)
                for source_path, final_path in files_to_move:
                    self.bundle.fs_service.move_file(source_path, final_path)

                self.bundle.update_audio_paths([Path(p) for _, p in files_to_move])
                self.bundle.init_metadata(
                    filenames=[p.name for _, p in files_to_move],
                )
                self.bundle.init_custom_context()

    def _validate_audio_files(
        self,
        files_to_move: list[tuple[Path, Path]],
        min_length: float,
    ) -> None:
        """Validate each audio file's duration before moving it into the bundle.

        Logs the affected file path on decoder failures, then re-raises so the
        caller's job loop can isolate the failure to this bundle.

        Raises:
            FileNotFoundError: If an audio file does not exist.
            CouldntDecodeError: If an audio file is unsupported or corrupted.
            TooShortException: If an audio file is shorter than min_length.
            Exception: For other unexpected decoder errors.

        """
        for source_path, _ in files_to_move:
            try:
                audio_length = self.bundle.audio_service.get_audio_duration(source_path)
            except FileNotFoundError:
                logger.error(f"Audio file not found: {source_path}")
                raise
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error(f"Failed to read audio file {source_path}: {type(e).__name__}: {e}")
                raise
            if min_length and audio_length < min_length:
                raise TooShortException(
                    source_audio=source_path,
                    audio_length=audio_length,
                    min_length=min_length,
                )


@dataclass
class TranscriptionJob(TranscribeBundleJob):
    """Transcribe audio files in the bundle."""

    @override
    def run(
        self,
        ai_manager: AIManager,
        config: TranscribeConfig,
        bundle_cache: BundleCache,
    ) -> None:
        """Main function.

        Args:
            ai_manager: AI operations used to transcribe source audio.
            config: Application configuration for the current run.
            bundle_cache: Loaded bundles indexed by persistent ID.

        Raises:
            FileNotFoundError: If bundle has no audio files set.
            EmptyTranscriptException: If transcription results in empty content.

        """
        if not self.bundle.source_audios:
            error_msg = f"{self}: Bundle has no audio files set"
            raise FileNotFoundError(error_msg)

        logger.info(f"{self.bundle}: Transcribing {len(self.bundle.source_audios)} audio file(s)")

        if not self.dry_run:
            transcripts: list[str] = []
            for audio_path in self.bundle.source_audios:
                logger.debug(f"Transcribing {audio_path}")
                transcript_content = ai_manager.transcribe_audio(audio_path)
                if transcript_content.strip() == "":
                    raise EmptyTranscriptException(source_audio=audio_path, context=f"{self}: Transcription resulted in empty transcript")
                transcripts.append(transcript_content)

            # Concatenate all transcripts
            concatenated = MULTIPLE_TRANSCRIPTS_SEPARATOR.join(transcripts)

            # Sync audio_files metadata to reflect current state (e.g. if new
            # files were added). Preserve any existing per-file transcript models.
            existing_models = {f.filename: f.transcript_model_used for f in self.bundle.metadata.audio_files}
            self.bundle.metadata.audio_files = [
                AudioFileMeta(
                    filename=audio.name,
                    transcript_model_used=existing_models.get(audio.name, []),
                )
                for audio in self.bundle.source_audios
            ]
            self.bundle.metadata.write(
                self.bundle.get_bundle_dir(),
                self.bundle.fs_service,
            )

            # Write transcript after metadata is updated
            self.bundle.set_and_write_transcript(concatenated)


@dataclass
class SummaryJob(TranscribeBundleJob):
    """Generate AI summary for the bundle based on transcript."""

    @override
    def run(
        self,
        ai_manager: AIManager,
        config: TranscribeConfig,
        bundle_cache: BundleCache,
    ) -> None:
        """Main function.

        Args:
            ai_manager: AI operations used to generate the summary.
            config: Application configuration for the current run.
            bundle_cache: Loaded bundles indexed by persistent ID.

        Raises:
            ValueError: If transcript is not available for summarization.

        """
        logger.info(f"{self.bundle}: Generating summary")

        if self.dry_run:
            return

        if not self.bundle.transcript:
            msg = f"{self}: Cannot generate ai summary without transcript"
            raise ValueError(msg)

        summary_content = ai_manager.get_ai_summary(
            self.bundle.transcript.text,
            record_context=self.bundle.get_effective_context(),
        )
        logger.info(f"Summary complete. Excerpt: {summary_content[:40]}")
        self.bundle.set_and_write_summary(summary_content)

        # Persist the context hash so the next run can detect whether the
        # user-edited custom_context.md requires regenerating the summary.
        self.bundle.metadata.summary_context_hash = self.bundle.compute_context_hash()
        # A summary change invalidates only an AI-generated title. A manual title
        # is an explicit user choice and remains authoritative.
        self.bundle.invalidate_generated_bundle_title()
        self.bundle.metadata.write(self.bundle.get_bundle_dir(), self.bundle.fs_service)


@dataclass
class BundleNameJob(TranscribeBundleJob):
    """Generate AI-based bundle name based on summary."""

    @override
    def run(
        self,
        ai_manager: AIManager,
        config: TranscribeConfig,
        bundle_cache: BundleCache,
    ) -> None:
        """Generate and persist a human-readable bundle name.

        Args:
            ai_manager: AI operations used to generate the name.
            config: Application configuration for the current run.
            bundle_cache: Loaded bundles indexed by persistent ID.

        """
        if not self.bundle.needs_naming():
            logger.debug(f"{self.bundle}: Skipping bundle naming because its title is no longer pending")
            return

        logger.info(f"{self.bundle}: Generating bundle name")
        if self.dry_run:
            return

        if not self.bundle.summary:
            msg = f"{self}: Cannot generate bundle name without summary"
            raise ValueError(msg)

        try:
            bundle_name = ai_manager.get_bundle_name_summary(self.bundle.summary.text)
        except Exception:
            logger.error(f"{self}: Failed to generate bundle name: {traceback.format_exc()}")
            raise

        logger.info(f"Generated bundle name: {bundle_name}")
        self.bundle.set_and_write_bundle_title(
            bundle_name,
            title_state=BundleTitleState.GENERATED,
        )


@dataclass
class DeleteAudioFileJob(TranscribeBundleJob):
    """Remove all audio files in bundle."""

    @override
    def run(
        self,
        ai_manager: AIManager,
        config: TranscribeConfig,
        bundle_cache: BundleCache,
    ) -> None:
        """Delete eligible source audio files.

        Args:
            ai_manager: AI operations supplied through the shared job interface.
            config: Application configuration for the current run.
            bundle_cache: Loaded bundles indexed by persistent ID.

        """
        if not self.bundle.source_audios:
            msg = f"{self}: Bundle has no audio files set"
            raise FileNotFoundError(msg)

        logger.info(f"Deleting {len(self.bundle.source_audios)} audio file(s)")
        if self.dry_run:
            return

        for audio_path in self.bundle.source_audios:
            logger.debug(f"Deleting {audio_path}")
            self.bundle.fs_service.delete_file(audio_path)

        self.bundle.update_audio_paths(None)


@dataclass
class GatherCommandsJob(TranscribeBundleJob):
    """Gather raw commands from transcription, not interpreting them yet."""

    @override
    def run(
        self,
        ai_manager: AIManager,
        config: TranscribeConfig,
        bundle_cache: BundleCache,
    ) -> None:
        """Extract and persist raw commands from the transcript.

        Args:
            ai_manager: AI operations used to extract commands.
            config: Application configuration for the current run.
            bundle_cache: Loaded bundles indexed by persistent ID.

        """
        logger.debug(f"Gathering commands for {self.bundle}")
        if self.dry_run:
            return

        if not self.bundle.transcript:
            msg = f"{self}: Cannot generate commands without transcript"
            raise ValueError(msg)

        commands: list[str] = []
        try:
            commands = ai_manager.extract_raw_commands(self.bundle.transcript.text, self.bundle.bundle_name)
            logger.debug(f"{self.bundle}: Extracted raw commands: {commands}")

        except Exception:
            logger.error(f"{self}: Failed to extract commands: {traceback.format_exc()}")
            raise

        # Remove duplicate raw commands
        # This doesn't remove potential duplicates after command matching,
        # but just the ones with the exact same prompt,
        # which is still important because the command text is used as identifier
        commands = list(dict.fromkeys(commands))

        logger.info(f"{self}: Successfully extracted commands (len {len(commands)})")
        self.bundle.set_and_write_commands(commands)


@dataclass
class RunCommandsJob(TranscribeBundleJob):
    """Try to run non-executed commands for a bundle."""

    action_runtime: ActionRuntime

    @override
    def run(
        self,
        ai_manager: AIManager,
        config: TranscribeConfig,
        bundle_cache: BundleCache,
    ) -> ActionEffects | None:
        """Execute pending commands for the bundle.

        Processes commands in priority order and applies each type's execution
        policy. Handles command execution, error recovery, and retry logic.

        Args:
            ai_manager: AI operations used while interpreting commands.
            config: Application configuration for command handlers.
            bundle_cache: Loaded bundles indexed by persistent ID.

        Raises:
            AbortRemainingBundleJobsException: If a command requests aborting remaining jobs.
            Exception: If command execution fails after max attempts.

        """
        if not self.bundle.commands:
            if self.dry_run:
                logger.info(f"{self.bundle}: (dry-run) Skipping running commands as they are not gathered")
                return None  # expected in dry run as commands might not have been gathered

            msg = f"{self}: Cannot run commands without commands file"
            raise ValueError(msg)

        if not self.bundle.commands.commands:
            return None  # no commands to run, that's valid

        if not any(cmd.needs_processing for cmd in self.bundle.commands.commands):
            logger.debug(f"All commands already resolved or submitted for {self.bundle}")
            return None

        logger.debug(f"Running commands for {self.bundle}")

        if self.dry_run:
            return None

        commands = self.bundle.commands.commands
        self._interpret_commands(ai_manager, commands)
        # Keep only commands that still have work to do
        pending_commands = [command for command in commands if command.needs_processing]
        pending_commands = self._select_commands_to_execute(pending_commands)

        # Commands with side effects that invalidate the rest of the bundle should run first.
        # Keep this local until a more complex command ordering system is actually needed.
        pending_commands.sort(key=lambda cmd: self._command_priority(cmd.matched_type))

        return self._execute_commands(pending_commands, config, bundle_cache)

    def _interpret_commands(
        self,
        ai_manager: AIManager,
        commands: list[Command],
    ) -> None:
        """Interpret unfinished commands that do not have a matched type.

        Args:
            ai_manager: AI service used to interpret commands.
            commands: Commands read from this bundle's command file.

        """
        for command in commands:
            if not command.needs_processing:
                continue

            if command.matched_type is None and command.needs_resolution:
                # This should not normally happen. Reinterpret a command that
                # needs work but is missing its type.
                interpretation = ai_manager.interpret_command(command.text)
                self.bundle.set_command_interpretation(command.id, interpretation)

    def _select_commands_to_execute(self, pending_commands: list[Command]) -> list[Command]:
        """Select pending commands to execute.

        Commands with a saved action request remain selected so that request can be
        completed. For command types that keep only the latest pending command, older
        commands are marked as superseded.

        Args:
            pending_commands: Commands that have been interpreted and still need work.

        Returns:
            list[Command]: Commands that should be executed now.

        """
        latest_by_type: dict[CommandType, Command] = {}
        for command in pending_commands:
            if command.has_active_request:
                continue
            matched_type = command.matched_type
            assert matched_type is not None
            definition = COMMAND_REGISTRY[matched_type]
            if definition.execution_policy is CommandExecutionPolicy.LATEST_PENDING:
                latest_by_type[matched_type] = command

        selected_commands: list[Command] = []
        for command in pending_commands:
            if command.has_active_request:
                # This command already has a saved request. Process that request before
                # considering newer commands of the same type.
                selected_commands.append(command)
                continue

            matched_type = command.matched_type
            assert matched_type is not None
            latest = latest_by_type.get(matched_type)
            if latest is not None and command.id != latest.id:
                # A newer command of this type replaces this one before either
                # command creates an action request.
                logger.info(
                    f"Skipping superseded {matched_type.value} command: {command.text}",
                )
                self.bundle.set_command_superseded(command.id)
                continue

            selected_commands.append(command)

        return selected_commands

    def _execute_commands(
        self,
        pending_commands: list[Command],
        config: TranscribeConfig,
        bundle_cache: BundleCache,
    ) -> ActionEffects | None:
        """Execute commands according to retry and repetition policies.

        Some command types run only once for a bundle, so later duplicates are skipped.
        For other types, only the newest pending command is run; for example, a newer title replaces an older pending title.

        Args:
            pending_commands: Commands that still require execution.
            config: Application configuration passed to command handlers.
            bundle_cache: Loaded bundles indexed by persistent ID.

        Returns:
            ActionEffects | None: Effects from the first action that changes bundle
                state, or None if no command changes a bundle.

        """
        assert self.bundle.commands
        # Resolved command types are used to enforce policies such as running
        # an IGNORE command only once for this bundle.
        seen_executed_types: set[CommandType] = {
            cmd.matched_type for cmd in self.bundle.commands.commands if cmd.is_resolved and cmd.matched_type is not None
        }

        for cmd in pending_commands:
            matched_type = cmd.matched_type
            assert matched_type is not None
            definition = COMMAND_REGISTRY[matched_type]

            if (
                not cmd.has_active_request
                and definition.execution_policy is CommandExecutionPolicy.ONCE_PER_BUNDLE
                and matched_type in seen_executed_types
            ):
                # This command type has already completed for the bundle, so
                # this later duplicate has no work left to do.
                logger.info(f"Skipping duplicate {matched_type.value} command: {cmd.text}")
                self.bundle.set_command_superseded(cmd.id)
                continue

            logger.info(f"Executing {matched_type.value} command for bundle {self.bundle}")

            if matched_type in {CommandType.MERGE, CommandType.DELETE, CommandType.SET_TITLE}:
                effects = self._process_state_changing_command(cmd, matched_type, config, bundle_cache)
                if effects is not None and (effects.changed_bundle_ids or effects.removed_bundle_ids):
                    # A state-changing action can make the remaining queued jobs
                    # stale. Return its effects so the caller can rebuild them.
                    return effects
                continue
            if matched_type is CommandType.IGNORE:
                self.bundle.set_command_ignored(cmd.id)
                seen_executed_types.add(matched_type)
                continue
            if matched_type is CommandType.UNKNOWN:
                self.bundle.set_command_rejected(cmd.id, "Interpretation produced no supported command type.")
                seen_executed_types.add(matched_type)
                continue

            msg = f"Unsupported command type: {matched_type}"
            raise AssertionError(msg)
        return None

    def _process_state_changing_command(
        self,
        command: Command,
        command_type: CommandType,
        config: TranscribeConfig,
        bundle_cache: BundleCache,
    ) -> ActionEffects | None:
        """Carry out a state-changing command through its durable action request."""
        request = self._get_or_create_command_request(command, command_type)
        if request.status == "pending":
            processed = self._process_action_request(request, bundle_cache)
        else:
            # An earlier run may have finished the request but stopped before
            # updating the command file. Rebuild its saved result without
            # running the action again.
            processed = ProcessedActionRequest(
                request=request,
                result=self._rebuild_finished_result(request),
            )

        self._acknowledge_command_result(
            command.id,
            processed.request,
            processed.result,
            bundle_cache,
            config,
        )
        return self._handle_action_result(
            processed.request.action,
            processed.result,
            processed.request.request_id,
        )

    def _get_or_create_command_request(
        self,
        command: Command,
        command_type: CommandType,
    ) -> ActionRequest:
        """Return the command's existing request or create it when missing.

        Args:
            command: State-changing command being processed.
            command_type: Validated type used to build a new action when needed.

        Returns:
            ActionRequest: Existing or newly created request for this command.

        Raises:
            ValueError: If the recorded request belongs to another command.

        """
        service = self.action_runtime.service
        resolution = command.resolution
        if isinstance(resolution, ActionRequestCommandResolution):
            # A previous run already recorded this ID in the command file. Reuse
            # it so a retry can find the same durable request after a crash.
            request_id = resolution.request_id
            request = service.get_request(request_id)
            if request is None:
                logger.warning(
                    f"Command {command.id} references action request {request_id}, but it was not found in the DB; creating it again.",
                )
            elif not isinstance(request.origin, CommandActionOrigin) or request.origin.command_id != command.id:
                msg = f"Command {command.id} references action request {request_id}, but that request belongs to a different command."
                raise ValueError(msg)
            else:
                return request
        else:
            request_id = new_action_request_id()
            # Record the ID before creating the request in DB. The two
            # stores (db + bundle files) cannot share a transaction, so this lets a retry resume
            # the same request if the process stops between these writes.
            self.bundle.set_command_resolution(
                command.id,
                ActionRequestCommandResolution(request_id=request_id),
            )

        action = self._build_action_from_command(command, command_type)
        origin = CommandActionOrigin(bundle_id=self.bundle.bundle_id, command_id=command.id)
        return service.submit(action, origin, request_id=request_id)

    def _process_action_request(
        self,
        request: ActionRequest,
        bundle_cache: BundleCache,
    ) -> ProcessedActionRequest:
        """Process a pending action request.

        Args:
            request: Pending request to process.
            bundle_cache: Bundles currently loaded by the update loop.

        Returns:
            ProcessedActionRequest: Finished request and its processing result.

        """
        processor = self.action_runtime.create_processor(bundle_cache)
        processed = processor.process(request.request_id)
        assert processed is not None  # A request loaded as pending must be processed.
        return processed

    def _build_action_from_command(self, command: Command, command_type: CommandType) -> Action:
        """Build the action described by a validated command."""
        # Commands always act on their own bundle, except a merge whose target
        # is selected later from the current bundle order.
        match command_type:
            case CommandType.MERGE:
                return MergeAction(
                    source_bundle_id=self.bundle.bundle_id,
                    target=PreviousBundleTarget(),
                )
            case CommandType.DELETE:
                return DeleteAction(bundle_id=self.bundle.bundle_id)
            case CommandType.SET_TITLE:
                # Only SET_TITLE commands carry title arguments. Check the
                # concrete type before using the value stored by interpretation.
                arguments = command.arguments
                if not isinstance(arguments, SetTitleCommandArguments):
                    msg = f"SET_TITLE command has invalid arguments: {command.text}"
                    raise TypeError(msg)
                return SetTitleAction(bundle_id=self.bundle.bundle_id, title=arguments.title)
            case _:
                msg = f"Command type does not create an action request: {command_type}"
                raise ValueError(msg)

    def _handle_action_result(
        self,
        action: Action,
        result: ActionResult,
        request_id: str,
    ) -> ActionEffects | None:
        """Return effects for a successful action; raise or log an unsuccessful result."""
        if isinstance(result, ActionSucceeded):
            # The caller uses these IDs to remove stale work and rebuild jobs
            # from the bundles changed by the action.
            return result.effects
        if isinstance(result, ActionBlocked):
            if isinstance(action, MergeAction):
                # A blocked merge leaves a marker that prevents automatic
                # repetition until a person has inspected the bundle state.
                raise MergeBlockedException(result.error.message)
            # Other blocked actions stop this bundle's remaining jobs without
            # preventing unrelated bundles from continuing.
            msg = f"Action request {request_id} blocked: {result.error.message}"
            raise AbortRemainingBundleJobsException(msg)
        # A failed action has already recorded its result in the command file.
        # Log it here, then allow the remaining bundle jobs to continue.
        logger.error(f"Action request {request_id} failed: {result.error.message}")
        return None

    @staticmethod
    def _rebuild_finished_result(request: ActionRequest) -> ActionResult:
        """Rebuild a finished request result after an interrupted update."""
        match request.status:
            case "succeeded":
                return ActionSucceeded()
            case "failed":
                assert request.error is not None
                return ActionFailed(error=request.error)
            case "blocked":
                assert request.error is not None
                return ActionBlocked(error=request.error)
            case _:
                msg = f"Action request {request.request_id} is not terminal: {request.status}"
                raise ValueError(msg)

    def _acknowledge_command_result(
        self,
        command_id: str,
        request: ActionRequest,
        result: ActionResult,
        bundle_cache: BundleCache,
        config: TranscribeConfig,
    ) -> None:
        """Write the finished request result back to the command file.

        The action result is already saved in the DB. The DB and command file
        cannot share one transaction, so write the result to the command file
        first, then mark the DB request as acknowledged. If the process stops
        between those writes, the request remains unacknowledged so a later run
        can safely retry this step.

        Args:
            command_id: ID of the command that started the action request.
            request: Finished request saved in the DB.
            result: Result to write into the command file.
            bundle_cache: Bundles currently loaded by the update loop.
            config: Configuration used for the fallback resolution timestamp.

        """
        service = self.action_runtime.service
        owner = self._find_command_bundle(command_id, bundle_cache)
        if owner is None:
            if isinstance(request.action, DeleteAction) and isinstance(result, ActionSucceeded):
                # Successful deletion removes the command file itself, leaving
                # no separate origin receipt to persist before acknowledgement.
                service.acknowledge(request.request_id)
                return
            msg = f"Could not locate command {command_id} while acknowledging request {request.request_id}"
            raise ValueError(msg)
        resolved_at = request.finished_at or datetime.now(config.general.timezone)
        owner.set_command_resolution(
            command_id,
            ActionRequestCommandResolution(
                request_id=request.request_id,
                outcome=result.status,
                resolved_at=resolved_at,
            ),
        )
        service.acknowledge(request.request_id)

    @staticmethod
    def _find_command_bundle(command_id: str, bundle_cache: BundleCache) -> TranscribeBundle | None:
        """Find the bundle that contains this command."""
        for bundle in bundle_cache.values():
            if bundle.commands and any(command.id == command_id for command in bundle.commands.commands):
                return bundle
        return None

    @staticmethod
    def _command_priority(command_type: CommandType | None) -> int:
        """Return execution priority. Lower values execute first."""
        match command_type:
            case CommandType.DELETE:
                return 0
            case CommandType.MERGE:
                return 1
            case _:
                return 2
