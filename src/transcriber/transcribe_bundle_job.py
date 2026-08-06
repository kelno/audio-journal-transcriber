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
from transcriber.actions.action_service import ActionService
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
        pending_commands = self._prepare_commands(ai_manager, commands)
        pending_commands = self._apply_execution_policies(pending_commands)

        # Commands with side effects that invalidate the rest of the bundle should run first.
        # Keep this local until a more complex command ordering system is actually needed.
        pending_commands.sort(key=lambda cmd: self._command_priority(cmd.matched_type))

        return self._execute_commands(pending_commands, config, bundle_cache)

    def _prepare_commands(
        self,
        ai_manager: AIManager,
        commands: list[Command],
    ) -> list[Command]:
        """Ensure commands have a matched type and return pending commands."""
        pending_commands = []

        for cmd in commands:
            if not cmd.needs_processing:
                continue

            if cmd.matched_type is None and cmd.needs_resolution:
                # Should not happen under normal circumstances, but recover from
                # inconsistent state if a command was marked executed without its type.
                interpretation = ai_manager.interpret_command(cmd.text)
                self.bundle.set_command_interpretation(cmd.id, interpretation)

            pending_commands.append(cmd)

        return pending_commands

    def _apply_execution_policies(self, pending_commands: list[Command]) -> list[Command]:
        """Apply each command type's policy and return the selected commands."""
        latest_by_type: dict[CommandType, Command] = {}
        for command in pending_commands:
            matched_type = command.matched_type
            assert matched_type is not None
            definition = COMMAND_REGISTRY[matched_type]
            if definition.execution_policy is CommandExecutionPolicy.LATEST_PENDING:
                latest_by_type[matched_type] = command

        selected_commands: list[Command] = []
        for command in pending_commands:
            matched_type = command.matched_type
            assert matched_type is not None
            latest = latest_by_type.get(matched_type)
            if latest is not None and command.id != latest.id:
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

        Once-per-bundle types skip later duplicates, while revision-style types
        may execute again when a new pending command is recorded.

        Args:
            pending_commands: Commands that still require execution.
            config: Application configuration passed to command handlers.
            bundle_cache: Loaded bundles indexed by persistent ID.

        """
        assert self.bundle.commands
        seen_executed_types: set[CommandType] = {
            cmd.matched_type for cmd in self.bundle.commands.commands if cmd.is_resolved and cmd.matched_type is not None
        }

        for cmd in pending_commands:
            matched_type = cmd.matched_type
            assert matched_type is not None
            definition = COMMAND_REGISTRY[matched_type]

            if definition.execution_policy is CommandExecutionPolicy.ONCE_PER_BUNDLE and matched_type in seen_executed_types:
                logger.info(
                    f"Skipping duplicate {matched_type.value} command: {cmd.text}",
                )
                self.bundle.set_command_superseded(cmd.id)
                continue

            logger.info(f"Executing {matched_type.value} command for bundle {self.bundle}")

            if matched_type in {CommandType.MERGE, CommandType.DELETE, CommandType.SET_TITLE}:
                effects = self._execute_action_request(cmd, matched_type, config, bundle_cache)
                if effects is not None and (effects.changed_bundle_ids or effects.removed_bundle_ids):
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

    def _execute_action_request(
        self,
        command: Command,
        command_type: CommandType,
        config: TranscribeConfig,
        bundle_cache: BundleCache,
    ) -> ActionEffects | None:
        """Submit, execute, and acknowledge one state-changing command."""
        action = self._action_from_command(command, command_type)
        origin = CommandActionOrigin(bundle_id=self.bundle.bundle_id, command_id=command.id)
        resolution = command.resolution
        if isinstance(resolution, ActionRequestCommandResolution):
            request_id = resolution.request_id
        else:
            request_id = new_action_request_id()
            self.bundle.set_command_resolution(
                command.id,
                ActionRequestCommandResolution(request_id=request_id),
            )

        service = self.action_runtime.service
        processor = self.action_runtime.processor(bundle_cache)
        request = service.submit(action, origin, request_id=request_id)
        result = processor.process(request_id) if request.status == "pending" else self._result_from_request(request)
        if result is None:
            request = service.get_request(request_id)
            assert request is not None
            result = self._result_from_request(request)

        request = service.get_request(request_id)
        assert request is not None
        self._acknowledge_command_result(
            command.id,
            request,
            result,
            service,
            bundle_cache,
            config,
            origin_may_be_removed=isinstance(action, DeleteAction) and isinstance(result, ActionSucceeded),
        )
        return self._handle_action_result(action, result, request_id)

    def _action_from_command(self, command: Command, command_type: CommandType) -> Action:
        """Translate a validated command into immutable action intent."""
        match command_type:
            case CommandType.MERGE:
                return MergeAction(
                    source_bundle_id=self.bundle.bundle_id,
                    target=PreviousBundleTarget(),
                )
            case CommandType.DELETE:
                return DeleteAction(bundle_id=self.bundle.bundle_id)
            case CommandType.SET_TITLE:
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
        """Return scheduler effects or translate a non-successful result."""
        if isinstance(result, ActionSucceeded):
            return result.effects
        if isinstance(result, ActionBlocked):
            if isinstance(action, MergeAction):
                raise MergeBlockedException(result.error.message)
            msg = f"Action request {request_id} blocked: {result.error.message}"
            raise AbortRemainingBundleJobsException(msg)
        logger.error(f"Action request {request_id} failed: {result.error.message}")
        return None

    @staticmethod
    def _result_from_request(request: ActionRequest) -> ActionResult:
        """Reconstruct a terminal result when acknowledging after a crash window."""
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

    @classmethod
    def _acknowledge_command_result(
        cls,
        command_id: str,
        request: ActionRequest,
        result: ActionResult,
        service: ActionService,
        bundle_cache: BundleCache,
        config: TranscribeConfig,
        *,
        origin_may_be_removed: bool,
    ) -> None:
        """Persist the terminal receipt before acknowledging its request row."""
        owner = cls._find_command_bundle(command_id, bundle_cache)
        if owner is None:
            if origin_may_be_removed:
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
        """Locate a stable command after an action may have moved its bundle."""
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
