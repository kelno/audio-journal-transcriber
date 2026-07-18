import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import override

from transcriber.audio_manipulation import AudioManipulation
from transcriber.commands.command import Command
from transcriber.commands.command_interpreter import extract_raw_commands, interpret_command
from transcriber.commands.command_registry import COMMAND_REGISTRY
from transcriber.commands.command_type import CommandType
from transcriber.constants import MULTIPLE_TRANSCRIPTS_SEPARATOR

from .ai_manager import AIManager
from .config import TranscribeConfig
from .exception import AbortRemainingBundleJobsException, EmptyTranscriptException, TooShortException
from .logger import logger
from .transcribe_bundle import TranscribeBundle
from .utils import ensure_directory_exists


@dataclass
class TranscribeBundleJob(ABC):
    """Abstract base class for all job types."""

    bundle: TranscribeBundle
    dry_run: bool

    @abstractmethod
    def run(self, ai_manager: AIManager, config: TranscribeConfig) -> None:
        """Perform the job's main work."""

    @override
    def __str__(self) -> str:
        """Return a string representation of the job."""
        return f"{self.__class__.__name__}({self.bundle.bundle_name})"


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
    ) -> None:
        """Move all audio files into the bundle directory.

        Raises:
            FileNotFoundError: If bundle has no audio files set.

        """
        if not self.bundle.source_audios:
            error_msg = "Bundle has no audio files set"
            raise FileNotFoundError(error_msg)

        final_audio_paths = self.bundle.get_bundle_audio_paths()
        audio_lengths: list[float] = []
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
                min_length = config.general.min_length_seconds

                # Validate all files
                for source_path, _ in files_to_move:
                    audio_length = AudioManipulation.get_audio_duration(source_path)
                    if min_length and audio_length < min_length:
                        raise TooShortException(
                            source_audio=source_path,
                            audio_length=audio_length,
                            min_length=min_length,
                        )
                    audio_lengths.append(audio_length)

                # Move all files
                ensure_directory_exists(final_audio_paths[0].parent)
                for source_path, final_path in files_to_move:
                    self.bundle.fs_service.move_file(source_path, final_path)

                self.bundle.update_audio_paths([Path(p) for _, p in files_to_move])
                self.bundle.init_metadata(
                    filenames=[p.name for _, p in files_to_move],
                    audio_lengths=audio_lengths,
                )


@dataclass
class TranscriptionJob(TranscribeBundleJob):
    """Transcribe audio files in the bundle."""

    @override
    def run(
        self,
        ai_manager: AIManager,
        config: TranscribeConfig,
    ) -> None:
        """Main function.

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
                    error_msg = f"{self}: Transcription of {audio_path} resulted in empty transcript"
                    raise EmptyTranscriptException(error_msg)
                transcripts.append(transcript_content)

            # Concatenate all transcripts
            concatenated = MULTIPLE_TRANSCRIPTS_SEPARATOR.join(transcripts)

            # Update original_audio_filenames to reflect current state
            # This ensures that if new files were added, metadata is synced
            self.bundle.metadata.original_audio_filenames = [audio.name for audio in self.bundle.source_audios]
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
    ) -> None:
        """Main function.

        Raises:
            ValueError: If transcript is not available for summarization.

        """
        logger.info(f"{self.bundle}: Generating summary")

        if self.dry_run:
            return

        if not self.bundle.transcript:
            msg = f"{self}: Cannot generate ai summary without transcript"
            raise ValueError(msg)

        summary_content = ai_manager.get_ai_summary(self.bundle.transcript.text)
        logger.info(f"Summary complete: {summary_content[:40]}")
        self.bundle.set_and_write_summary(summary_content)


@dataclass
class BundleNameJob(TranscribeBundleJob):
    """Generate AI-based bundle name based on summary."""

    @override
    def run(
        self,
        ai_manager: AIManager,
        config: TranscribeConfig,
    ) -> None:
        """Main function."""
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
        self.bundle.set_and_write_bundle_name(bundle_name)


@dataclass
class DeleteAudioFileJob(TranscribeBundleJob):
    """Remove all audio files in bundle."""

    @override
    def run(
        self,
        ai_manager: AIManager,
        config: TranscribeConfig,
    ) -> None:
        """Main function."""
        if not self.bundle.source_audios:
            msg = f"{self}: Bundle has no audio files set"
            raise FileNotFoundError(msg)

        logger.info(f"Deleting {len(self.bundle.source_audios)} audio file(s)")
        if self.dry_run:
            return

        for audio_path in self.bundle.source_audios:
            logger.debug(f"Deleting {audio_path}")
            audio_path.unlink()

        self.bundle.update_audio_paths(None)


@dataclass
class GatherCommandsJob(TranscribeBundleJob):
    """Gather raw commands from transcription, not interpreting them yet."""

    @override
    def run(
        self,
        ai_manager: AIManager,
        config: TranscribeConfig,
    ) -> None:
        """Main function."""
        logger.debug(f"Gathering commands for {self.bundle}")
        if self.dry_run:
            return

        if not self.bundle.transcript:
            msg = f"{self}: Cannot generate commands without transcript"
            raise ValueError(msg)

        commands: list[str] = []
        try:
            commands = extract_raw_commands(self.bundle.transcript.text, self.bundle.bundle_name, ai_manager)
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

    @override
    def run(
        self,
        ai_manager: AIManager,
        config: TranscribeConfig,
    ) -> None:
        if not self.bundle.commands:
            if self.dry_run:
                logger.info(f"{self.bundle}: (dry-run) Skipping running commands as they are not gathered")
                return  # expected in dry run as commands might not have been gathered

            msg = f"{self}: Cannot run commands without commands file"
            raise ValueError(msg)

        if not self.bundle.commands.commands:
            return  # no commands to run, that's valid

        if not any(not cmd.executed for cmd in self.bundle.commands.commands):
            logger.debug(f"All commands already executed for {self.bundle}")
            return

        logger.debug(f"Running commands for {self.bundle}")

        if self.dry_run:
            return

        commands = self.bundle.commands.commands
        pending_commands = self._prepare_commands(ai_manager, commands)

        # Commands with side effects that invalidate the rest of the bundle should run first.
        # Keep this local until a more complex command ordering system is actually needed.
        pending_commands.sort(key=lambda cmd: self._command_priority(cmd.matched_type))

        self._execute_commands(pending_commands, config)

    def _prepare_commands(
        self,
        ai_manager: AIManager,
        commands: list[Command],
    ) -> list[Command]:
        """Ensure commands have a matched type and return pending commands."""
        pending_commands = []

        for cmd in commands:
            if cmd.executed:
                continue

            if cmd.matched_type is None:
                # Should not happen under normal circumstances, but recover from
                # inconsistent state if a command was marked executed without its type.
                matched_type = interpret_command(cmd.text, ai_manager)
                self.bundle.set_command_type(cmd.text, matched_type)
                cmd.matched_type = matched_type

            pending_commands.append(cmd)

        return pending_commands

    def _execute_commands(
        self,
        pending_commands: list[Command],
        config: TranscribeConfig,
    ) -> None:
        """Execute commands while avoiding duplicate command types.

        A bundle only ever has one effective command execution per command type, even across multiple job runs.
        """
        assert self.bundle.commands
        seen_executed_types: set[CommandType] = {
            cmd.matched_type for cmd in self.bundle.commands.commands if cmd.executed and cmd.matched_type is not None
        }

        for cmd in pending_commands:
            try:
                matched_type = cmd.matched_type
                assert matched_type is not None

                if matched_type in seen_executed_types:
                    logger.info(
                        f"Skipping duplicate {matched_type.value} command: {cmd.text}",
                    )

                    # Mark as executed to avoid picking it up when gathering bundle with pending commands.
                    self.bundle.set_command_executed(cmd.text)
                    continue

                logger.info(f"Executing {matched_type.value} command for bundle {self.bundle}")

                handler = COMMAND_REGISTRY[matched_type].handler
                handler(self.bundle, config, cmd.text)

                self.bundle.set_command_executed(cmd.text)
                seen_executed_types.add(matched_type)

            except AbortRemainingBundleJobsException:
                logger.debug(f"{cmd} requested aborting remaining jobs for bundle {self.bundle}")
                raise  # raise it further to the job execution loop

            except Exception as e:
                # On error, stop processing remaining commands to avoid partial state.
                logger.exception(
                    f"Failed to process bundle {self.bundle} command '{cmd.text}'",
                )
                self.bundle.set_last_error(cmd_text=cmd.text, error=str(e))
                raise

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
