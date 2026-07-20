from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import override

from transcriber.audio_service import AudioService
from transcriber.commands.command import Command
from transcriber.commands.command_type import CommandType
from transcriber.config import TranscribeConfig
from transcriber.constants import (
    COMMANDS_FILENAME,
    CUSTOM_CONTEXT_FILENAME,
    DEFAULT_CUSTOM_CONTEXT_CONTENT,
    METADATA_FILENAME,
    SUMMARY_FILENAME,
    TRANSCRIPT_FILENAME,
)
from transcriber.exception import FailedToExtractDateException, InvalidBundleException, InvalidSourceAudiosException
from transcriber.files.commands_file import CommandsFile
from transcriber.files.file_system import FileSystemService
from transcriber.files.metadata import AudioFileMeta, MetadataFile
from transcriber.files.text_file import (
    CustomContextFile,
    SummaryFile,
    TranscriptFile,
)
from transcriber.globals import is_handled_audio_file
from transcriber.logger import logger
from transcriber.utils import (
    extract_date_from_recording_filename,
    file_is_in_directory_tree,
    get_days_since_time,
    get_file_modified_date,
)


@dataclass
class TranscribeBundle:
    fs_service: FileSystemService
    audio_service: AudioService
    config: TranscribeConfig

    bundle_name: str  # directory name is derived from here
    metadata: MetadataFile
    source_audios: list[Path] = field(default_factory=list)
    transcript: TranscriptFile | None = None
    summary: SummaryFile | None = None
    commands: CommandsFile | None = None
    custom_context: CustomContextFile | None = None

    @override
    def __str__(self) -> str:
        return f"[Bundle:{self.bundle_name}]"

    @staticmethod
    def _load_bundle_files(
        existing_dir: Path,
        fs_service: FileSystemService,
    ) -> tuple[
        MetadataFile,
        list[Path],
        TranscriptFile | None,
        SummaryFile | None,
        CommandsFile | None,
        CustomContextFile | None,
    ]:
        """Load audio paths and text files from bundle directory."""
        meta_file_path = existing_dir / METADATA_FILENAME
        if not fs_service.file_exists(meta_file_path):
            error_msg = f"Bundle directory is invalid (no meta file): {existing_dir}"
            raise InvalidBundleException(error_msg)

        metadata = MetadataFile.from_file(meta_file_path, fs_service)

        source_audios: list[Path] = []
        transcript: TranscriptFile | None = None
        summary: SummaryFile | None = None
        commands: CommandsFile | None = None
        custom_context: CustomContextFile | None = None

        for file_path in fs_service.list_directory(existing_dir):
            if is_handled_audio_file(file_path.suffix):
                source_audios.append(file_path)
            elif file_path.name == TRANSCRIPT_FILENAME:
                transcript = TranscriptFile.from_file(file_path, fs_service)
            elif file_path.name == SUMMARY_FILENAME:
                summary = SummaryFile.from_file(file_path, fs_service)
            elif file_path.name == COMMANDS_FILENAME:
                commands = CommandsFile.from_file(file_path, fs_service)
            elif file_path.name == CUSTOM_CONTEXT_FILENAME:
                custom_context = CustomContextFile.from_file(file_path, fs_service)

        return metadata, source_audios, transcript, summary, commands, custom_context

    @classmethod
    def from_existing_directory(
        cls,
        existing_dir: Path,
        config: TranscribeConfig,
        fs_service: FileSystemService,
        audio_service: AudioService,
    ) -> "TranscribeBundle":
        """Load an existing saved TranscribeBundle instance from a directory.

        Args:
            existing_dir: Path to the existing bundle directory.
            config: The configuration object containing timezone and other settings.
            fs_service: FileSystemService instance for file operations.
            audio_service: AudioManipulation instance for audio operations. If None, creates new instance.

        Returns:
            TranscribeBundle: The loaded bundle instance.

        Raises:
            InvalidSourceAudiosException: If the bundle directory is invalid.

        """
        bundle_name = existing_dir.name
        metadata, source_audios, transcript, summary, commands, custom_context = cls._load_bundle_files(
            existing_dir,
            fs_service,
        )

        # Sort audio files chronologically
        source_audios = cls.sort_audio_files_chronologically(source_audios, config)

        bundle = TranscribeBundle(
            bundle_name=bundle_name,
            metadata=metadata,
            source_audios=source_audios,
            transcript=transcript,
            summary=summary,
            commands=commands,
            custom_context=custom_context,
            fs_service=fs_service,
            audio_service=audio_service,
            config=config,
        )
        return bundle

    @classmethod
    def from_audio_file(
        cls,
        source_audio: Path,
        config: TranscribeConfig,
        fs_service: FileSystemService,
        audio_service: AudioService,
    ) -> "TranscribeBundle":
        """Create a new TranscribeBundle instance from an audio file."""
        bundle_date = TranscribeBundle.get_date_for_filename(source_audio, source_audio.name, config)
        metadata = MetadataFile(
            audio_files=[AudioFileMeta(filename=source_audio.name)],
            bundle_date=bundle_date,
        )
        bundle_name = TranscribeBundle.generate_generic_bundle_name(
            bundle_date=bundle_date,
            audio_filename=source_audio.name,
        )
        return cls(
            bundle_name=bundle_name,
            metadata=metadata,
            source_audios=[source_audio],
            fs_service=fs_service,
            audio_service=audio_service,
            config=config,
        )

    @classmethod
    def from_audio_files(
        cls,
        source_audios: list[Path],
        config: TranscribeConfig,
        fs_service: FileSystemService,
        audio_service: AudioService,
        bundle_name: str | None = None,
    ) -> "TranscribeBundle":
        """Create a new TranscribeBundle from multiple audio files."""
        if not source_audios:
            msg = "Must provide at least one audio file"
            raise InvalidSourceAudiosException(msg)

        filenames = [f.name for f in source_audios]
        bundle_date = TranscribeBundle.get_date_for_filename(
            source_audios[0],
            source_audios[0].name,
            config,
        )
        metadata = MetadataFile(
            audio_files=[AudioFileMeta(filename=name) for name in filenames],
            bundle_date=bundle_date,
        )

        # Use first file for date generation if no bundle_name provided
        if not bundle_name:
            bundle_name = cls.generate_generic_bundle_name(
                bundle_date=bundle_date,
                audio_filename=source_audios[0].name,
            )

        return cls(
            bundle_name=bundle_name,
            metadata=metadata,
            source_audios=source_audios,
            fs_service=fs_service,
            audio_service=audio_service,
            config=config,
        )

    def cleanup_inconsistencies(
        self,
        dry_run: bool,
    ) -> None:
        """Cleanup known existing bundle inconsistencies. Must be called explictily.

        Will remove summary and/or commands (both in this instance and in files) if no transcript file exists.
        Will refresh the whole bundle if the audio files are inconsistent with the metadata.
        """
        if self.summary and not self.transcript:
            self.summary = None
            logger.warning(
                f"Summary file found without transcript file for {self}. Removing summary file.",
            )
            if not dry_run:
                self.fs_service.delete_file(self.get_bundle_dir() / SUMMARY_FILENAME)
        if self.commands and not self.transcript:
            self.commands = None
            logger.warning(
                f"Commands file found without transcript file for {self}. Removing commands file.",
            )
            if not dry_run:
                self.fs_service.delete_file(self.get_bundle_dir() / COMMANDS_FILENAME)

        # Check if existing audio files match metadata
        original_audio_filenames = {f.filename for f in self.metadata.audio_files}
        current_audio_filenames = {f.name for f in self.source_audios}
        if current_audio_filenames and current_audio_filenames != original_audio_filenames:
            logger.info(
                f"Bundle {self.bundle_name} has audio files not matching metadata. "
                f"Original: {original_audio_filenames}, Found: {current_audio_filenames}",
            )
            self.refresh(dry_run)

        # Warn if the directory name's date prefix diverges from the canonical
        # bundle_date (e.g. the directory was renamed externally). Metadata is the
        # source of truth; we do not auto-rename, but surface the inconsistency.
        name_prefix = self.bundle_name[:10]
        try:
            name_date = datetime.strptime(name_prefix, "%Y-%m-%d").replace(tzinfo=self.config.general.timezone)
        except ValueError:
            name_date = None
        if name_date is not None and name_date.date() != self.metadata.bundle_date.date():
            logger.warning(
                f"{self}: Bundle directory name date [{name_prefix}] differs from canonical "
                f"metadata.bundle_date [{self.metadata.bundle_date.date()}]. "
                f"Metadata is authoritative; the directory may have been renamed.",
            )

    @staticmethod
    def sort_audio_files_chronologically(
        audio_files: list[Path],
        config: TranscribeConfig,
    ) -> list[Path]:
        """Sort audio files by extracted filename date, or fallback to modification time."""
        tz = config.general.timezone

        def get_sort_key(file_path: Path) -> tuple[int, datetime]:
            # Try to extract date from filename
            filename_date = extract_date_from_recording_filename(file_path.name, tz)
            if filename_date:
                return (0, filename_date)  # (0, date) - use filename date

            # Fallback to file modification time
            mod_date = get_file_modified_date(file_path, tz)
            return (1, mod_date)  # (1, date) - use mod time (secondary sort)

        return sorted(audio_files, key=get_sort_key)

    def assert_source_audios(
        self,
    ) -> list[Path]:
        """Ensure bundle has audio files, raise if empty."""
        if not self.source_audios:
            msg = f"Bundle {self.get_bundle_dir()} has no audio files set"
            raise FileNotFoundError(msg)
        return self.source_audios

    def refresh(self, dry_run: bool) -> None:
        """Clear transcript, summary, and bundle name to mark bundle for reprocessing.

        Used when bundle content changes (e.g., new audio files added).
        """
        logger.info(f"Refreshingbundle {self.bundle_name}")
        self.metadata.bundle_name_generated = False
        if not dry_run:
            self.metadata.write(self.get_bundle_dir(), self.fs_service)
            if self.transcript:
                self.transcript.unlink(self.get_bundle_dir(), self.fs_service)
            if self.summary:
                self.summary.unlink(self.get_bundle_dir(), self.fs_service)
            if self.commands:
                self.commands.unlink(self.get_bundle_dir(), self.fs_service)

        self.transcript = None
        self.summary = None
        self.commands = None

    @staticmethod
    def get_date_for_filename(
        audio_path: Path | None,
        audio_filename: str,
        config: TranscribeConfig,
    ) -> datetime:
        """Try to extract date from filename first, or else from file modified date.

        Raises:
           FailedToExtractDateException

        """
        tz = config.general.timezone
        date_from_filename = extract_date_from_recording_filename(audio_filename, tz)
        if date_from_filename:
            logger.debug(
                f"Found existing date [{date_from_filename}] in audio filename",
            )
            return date_from_filename
        elif audio_path:
            file_date = get_file_modified_date(audio_path, tz)
            logger.debug(
                f"No date found in filename, using file modified date : '{file_date}'",
            )
            return file_date
        else:
            msg = f"Could not find any date for file {audio_filename}"
            raise FailedToExtractDateException(msg)

    @staticmethod
    def generate_bundle_name_date_prefix(
        bundle_date: datetime,
    ) -> str:
        """Return a date prefix string in the 'YYYY-MM-DD' format."""
        return bundle_date.strftime("%Y-%m-%d")

    @classmethod
    def generate_generic_bundle_name(
        cls,
        bundle_date: datetime,
        audio_filename: str,
    ) -> str:
        """Generate a bundle name based on date and audio filename."""
        logger.debug(f"Generating bundle name for audio file: [{audio_filename}]")

        prefix = cls.generate_bundle_name_date_prefix(bundle_date)
        return f"{prefix}_{Path(audio_filename).stem}"

    def audio_source_needs_removal(
        self,
        store_dir: Path,
    ) -> bool:
        """Check if the bundle audio file can be removed.

        Won't remove files if the transcript does not exists.
        The date is either the latest audio file modification date or the bundle date, whichever is latest.
        """
        # We never want to remove the audio file if the transcript is not yet created
        if not self.transcript:
            return False

        # Bundle is explicitely marked as "keep audio forever"
        if self.metadata.keep_forever:
            return False

        # No audios to delete or removal is disabled in configuration
        if not self.source_audios or self.config.general.delete_source_audio_after_days <= 0:
            return False

        bundle_date = self.get_date_from_bundle_name()
        bundle_days_since = get_days_since_time(bundle_date)
        file_days_since = 0

        # Check all files, use the most recent one (max days_since)
        tz = self.config.general.timezone
        for audio_path in self.source_audios:
            # if any audio file is NOT in the store, we're in an unexpected state, don't remove anything
            if not file_is_in_directory_tree(audio_path, store_dir):
                logger.error(
                    f"bundle: {self}: audio_source_needs_removal has unexpectedly found an audio file outside of store: {audio_path}",
                )
                return False

            file_date = get_file_modified_date(audio_path, tz)
            file_days_since_current = get_days_since_time(file_date)
            file_days_since = min(file_days_since, file_days_since_current)

        days_since = min(bundle_days_since, file_days_since)
        return days_since > self.config.general.delete_source_audio_after_days

    def get_date_from_bundle_name(
        self,
    ) -> datetime:
        """Extract date from the bundle name."""
        # Date is in format %Y-%m-%d at the start of the bundle name
        date_str = self.bundle_name[0:10]
        return datetime.strptime(date_str, "%Y-%m-%d").replace(
            tzinfo=self.config.general.timezone,
        )

    def get_bundle_date(
        self,
    ) -> datetime:
        """Get the canonical date of the bundle.

        The single source of truth is ``metadata.bundle_date`` (a precise datetime
        computed once at creation).
        """
        return self.metadata.bundle_date

    @classmethod
    def find_previous_bundle(
        cls,
        current_bundle: "TranscribeBundle",
        bundles: list["TranscribeBundle"],
        max_hours: float | None = None,
    ) -> "TranscribeBundle | None":
        """Find the most recent bundle before the current bundle within a time window.

        IO heavy if many bundles exist, as it will check all bundle dates.
        """
        if max_hours is None:
            max_hours = current_bundle.config.general.merge_max_hours

        current_bundle_date = current_bundle.get_bundle_date()
        previous_candidates = [
            bundle
            for bundle in bundles
            if bundle.bundle_name != current_bundle.bundle_name and bundle.get_bundle_date() < current_bundle_date
        ]

        if not previous_candidates:
            return None

        previous_candidates.sort(
            key=lambda bundle: bundle.get_bundle_date(),
            reverse=True,
        )

        previous_bundle = previous_candidates[0]
        if current_bundle_date - previous_bundle.get_bundle_date() <= timedelta(hours=max_hours):
            return previous_bundle

        return None

    @staticmethod
    def gather_existing_bundles(
        store_dir: Path,
        dry_run: bool,
        cleanup_bundle: bool,  # run bundle.cleanup_inconsistencies on found bundles
        config: TranscribeConfig,
        fs_service: FileSystemService,
        audio_service: AudioService,
    ) -> list["TranscribeBundle"]:
        """Find and load all bundles from output_dir."""
        bundles: list[TranscribeBundle] = []
        for dir_path in fs_service.list_directory(store_dir):
            # Exclude if the directory starts with an underscore or is an hidden file
            if dir_path.name.startswith("_") or dir_path.name.startswith("."):
                continue
            if fs_service.directory_exists(dir_path):
                try:
                    bundle = TranscribeBundle.from_existing_directory(
                        dir_path,
                        config,
                        fs_service,
                        audio_service,
                    )
                    if cleanup_bundle:
                        bundle.cleanup_inconsistencies(dry_run)
                    bundles.append(bundle)
                except InvalidBundleException:
                    logger.exception(f"Skipping invalid transcribe bundle {dir_path}")
                except Exception:
                    logger.exception(f"Unexpected exception met while gathering bundle {dir_path}")

        logger.debug(f"Found {len(bundles)} existing bundles")
        return bundles

    def needs_naming(self) -> bool:
        """Check if the bundle needs a generated name."""
        return not self.metadata.bundle_name_generated

    def get_bundle_dir(self) -> Path:
        """Get the bundle directory path."""
        return self.config.general.store_dir / self.bundle_name

    def get_bundle_audio_paths(self) -> list[Path]:
        """Get all audio file paths within the bundle dir."""
        bundle_dir = self.get_bundle_dir()
        return [bundle_dir / audio.name for audio in self.source_audios]

    def update_audio_paths(self, new_audio_paths: list[Path] | None) -> None:
        """Update the source audio paths."""
        self.source_audios = new_audio_paths or []

    def set_and_write_transcript(
        self,
        transcript: str,
    ) -> None:
        """Set and write the transcript to memory and disk."""
        # Record the transcript model on the first audio file's metadata.
        if self.metadata.audio_files:
            self.metadata.audio_files[0].transcript_model_used = [self.config.audio.model]
        self.metadata.write(self.get_bundle_dir(), self.fs_service)
        self.transcript = TranscriptFile(transcript)
        self.transcript.write(self.get_bundle_dir(), self.fs_service)

    def set_and_write_summary(
        self,
        summary: str,
    ) -> None:
        """Set and write the summary to memory and disk."""
        self.metadata.summary_model_used = self.config.text.model
        self.metadata.write(self.get_bundle_dir(), self.fs_service)
        self.summary = SummaryFile(summary)
        self.summary.write(self.get_bundle_dir(), self.fs_service)

    def get_effective_context(self) -> str:
        """Return the user-provided context with comment lines and whitespace removed.

        Markdown comment lines starting with ``[//]:`` are ignored so the
        pregenerated header never counts as real content. A missing file or a
        file containing only the default header both resolve to an empty string,
        which keeps the summary stable across context-file additions/removals.
        """
        if self.custom_context is None:
            return ""
        lines = [line for line in self.custom_context.text.splitlines() if not line.lstrip().startswith("[//]:")]
        return "\n".join(lines).strip()

    def compute_context_hash(self) -> str:
        """Return a short hash of the effective context for change detection.

        Uses sha256 over the effective context; truncated to 16 hex characters.
        Cheap for the small inputs involved and stable across runs.
        """
        effective = self.get_effective_context()
        return sha256(effective.encode("utf-8")).hexdigest()[:16]

    def set_and_write_commands(
        self,
        commands: list[str],
    ) -> None:
        """Set and write the commands to memory and disk."""
        self.commands = CommandsFile.from_command_list(commands)
        self.commands.write(self.get_bundle_dir(), self.fs_service)

    def assert_command(self, cmd_id: str) -> Command:
        """Get a command by text. Command must exists, else throws exception."""
        if not self.commands:
            msg = "Trying to set command matched but bundle has no command"
            raise ValueError(msg)

        for cmd in self.commands.commands:
            if cmd.id == cmd_id:  # we identify command by text
                return cmd

        msg = f"Failed to find command {cmd_id} in bundle {self}"
        raise ValueError(msg)

    def set_command_type(self, cmd_id: str, matched_type: CommandType) -> None:
        """Mark a raw command with it's matched type."""
        cmd = self.assert_command(cmd_id)
        cmd.matched_type = matched_type

        assert self.commands is not None
        self.commands.write(self.get_bundle_dir(), self.fs_service)

    def set_command_executed(self, cmd_id: str) -> None:
        """Mark a raw command as executed."""
        cmd = self.assert_command(cmd_id)
        cmd.executed = True
        cmd.executed_at = datetime.now(self.config.general.timezone)

        assert self.commands is not None
        self.commands.write(self.get_bundle_dir(), self.fs_service)

    def set_last_error(self, cmd_id: str, error: object) -> None:
        """Set a debug error string for a command and write the commands file.

        Coerces `error` to `str`, normalizes newlines, and truncates very long
        messages to keep the commands YAML file compact. YAML serialization
        (`yaml.dump`) will safely encode multiline strings, so no escaping is
        required beyond ensuring we have a textual value.
        """
        cmd = self.assert_command(cmd_id)

        # Sanitize: Coerce to string and normalize newlines
        error_str = str(error) if error is not None else ""
        error_str = error_str.replace("\r\n", "\n").strip()

        # Sanitize: Truncate overly long messages to avoid huge YAML blobs
        max_len = 2000
        if len(error_str) > max_len:
            error_str = error_str[: max_len - 14] + "... (truncated)"

        cmd.last_error = error_str

        assert self.commands is not None
        self.commands.write(self.get_bundle_dir(), self.fs_service)

    def add_command_attempt(self, cmd_text: str) -> None:
        """Increment retry count by 1."""
        cmd = self.assert_command(cmd_text)
        cmd.attempt_count = cmd.attempt_count + 1

        assert self.commands is not None
        self.commands.write(self.get_bundle_dir(), self.fs_service)

    def init_metadata(
        self,
        filenames: list[str],
    ) -> None:
        """Initialize metadata with multiple files."""
        self.metadata.audio_files = [AudioFileMeta(filename=name) for name in filenames]
        self.metadata.write(self.get_bundle_dir(), self.fs_service)

    def init_custom_context(self) -> None:
        """Seed the per-bundle context file so the user has a discoverable place to add summary context.

        Possible cases in order of priority:
        - self.custom_context is already set and can be used
        - File already exists on disk and can be used
        - A new empty file should be created
        Use self.custom_context if already exists
        Only create it when missing so we never overwrite user-provided content on re-runs.
        """
        custom_context_path = self.get_bundle_dir() / CustomContextFile.get_filename()
        if self.custom_context:
            self.custom_context.write(self.get_bundle_dir(), self.fs_service)
        elif self.fs_service.file_exists(custom_context_path):
            self.custom_context = CustomContextFile.from_file(custom_context_path, self.fs_service)
        else:
            self.custom_context = CustomContextFile(DEFAULT_CUSTOM_CONTEXT_CONTENT)
            self.custom_context.write(self.get_bundle_dir(), self.fs_service)

        self.metadata.summary_context_hash = self.compute_context_hash()
        self.metadata.write(self.get_bundle_dir(), self.fs_service)

    def set_and_write_bundle_name(
        self,
        bundle_name_summary: str,
    ) -> None:
        """Set bundle name and rename the directory."""
        bundle_path_from = self.config.general.store_dir / self.bundle_name
        if not self.fs_service.directory_exists(bundle_path_from):
            msg = f"Bundle directory {bundle_path_from} not found"
            raise FileNotFoundError(msg)

        prefix = self.generate_bundle_name_date_prefix(self.metadata.bundle_date)
        new_bundle_name = f"{prefix} {bundle_name_summary}"

        bundle_path_to = self.config.general.store_dir / new_bundle_name
        self.fs_service.rename_directory(bundle_path_from, bundle_path_to)
        self.bundle_name = new_bundle_name

        self.metadata.bundle_name_generated = True
        self.metadata.write(self.get_bundle_dir(), self.fs_service)
