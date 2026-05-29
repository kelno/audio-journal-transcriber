from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from transcriber.config import TranscribeConfig
from transcriber.constants import (
    COMMANDS_FILENAME,
    METADATA_FILENAME,
    SUMMARY_FILENAME,
    TRANSCRIPT_FILENAME,
)
from transcriber.file_system import FileSystemService, RealFileSystemService
from transcriber.text_file import CommandsFile, SummaryFile, TextFile, TranscriptFile

from .globals import is_handled_audio_file
from .logger import logger
from .metadata import MetadataFile
from .utils import (
    extract_date_from_recording_filename,
    get_days_since_time,
    get_file_modified_date,
)


@dataclass
class TranscribeBundle:
    fs_service: FileSystemService
    config: TranscribeConfig

    bundle_name: str  # directory name is derived from here
    metadata: MetadataFile
    source_audios: list[Path] = field(default_factory=list)
    transcript: TextFile | None = None
    summary: TextFile | None = None
    commands: TextFile | None = None

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
    ]:
        """Load audio paths and text files from bundle directory."""
        meta_file_path = existing_dir / METADATA_FILENAME
        if not fs_service.file_exists(meta_file_path):
            error_msg = "Bundle directory is invalid (no meta file)"
            raise ValueError(error_msg)

        metadata = MetadataFile.from_file(meta_file_path, fs_service)

        source_audios: list[Path] = []
        transcript: TranscriptFile | None = None
        summary: SummaryFile | None = None
        commands: CommandsFile | None = None

        for file_path in fs_service.list_directory(existing_dir):
            if is_handled_audio_file(file_path.suffix):
                source_audios.append(file_path)
            elif file_path.name == TRANSCRIPT_FILENAME:
                transcript = TranscriptFile.from_file(file_path, fs_service)
            elif file_path.name == SUMMARY_FILENAME:
                summary = SummaryFile.from_file(file_path, fs_service)
            elif file_path.name == COMMANDS_FILENAME:
                commands = CommandsFile.from_file(file_path, fs_service)

        return metadata, source_audios, transcript, summary, commands

    @classmethod
    def from_existing_directory(
        cls,
        existing_dir: Path,
        dry_run: bool,
        config: TranscribeConfig,
        fs_service: FileSystemService,
    ) -> "TranscribeBundle":
        """Load an existing saved TranscribeBundle instance from a directory.

        Args:
            existing_dir: Path to the existing bundle directory.
            dry_run: Whether to simulate operations without making changes.
            config: The configuration object containing timezone and other settings.
            fs_service: FileSystemService instance for file operations.

        Returns:
            TranscribeBundle: The loaded bundle instance.

        Raises:
            ValueError: If the bundle directory is invalid.

        """
        bundle_name = existing_dir.name
        metadata, source_audios, transcript, summary, commands = cls._load_bundle_files(
            existing_dir,
            fs_service,
        )

        # Sort audio files chronologically
        source_audios = cls._sort_audio_files_chronologically(source_audios, config)

        # Check if there are new unprocessed audio files
        found_filenames = {f.name for f in source_audios}
        original_filenames = set(metadata.original_audio_filenames)

        bundle = TranscribeBundle(
            bundle_name=bundle_name,
            metadata=metadata,
            source_audios=source_audios,
            transcript=transcript,
            summary=summary,
            commands=commands,
            fs_service=fs_service,
            config=config,
        )

        if found_filenames != original_filenames:
            logger.info(
                f"Bundle {bundle_name} has new unprocessed audio files. "
                f"Original: {original_filenames}, Found: {found_filenames}",
            )
            bundle.refresh(dry_run)

        return bundle

    @classmethod
    def from_audio_file(
        cls,
        source_audio: Path,
        config: TranscribeConfig,
        fs_service: FileSystemService,
    ) -> "TranscribeBundle":
        """Create a new TranscribeBundle instance from an audio file."""
        metadata = MetadataFile(original_audio_filenames=[source_audio.name])
        bundle_name = TranscribeBundle.generate_generic_bundle_name(
            source_audio,
            source_audio.name,
            config,
        )

        return cls(
            bundle_name=bundle_name,
            metadata=metadata,
            source_audios=[source_audio],
            fs_service=fs_service,
            config=config,
        )

    @classmethod
    def from_audio_files(
        cls,
        source_audios: list[Path],
        config: TranscribeConfig,
        bundle_name: str | None = None,
        fs_service: FileSystemService | None = None,
    ) -> "TranscribeBundle":
        """Create a new TranscribeBundle from multiple audio files."""
        if not source_audios:
            msg = "Must provide at least one audio file"
            raise ValueError(msg)

        filenames = [f.name for f in source_audios]
        metadata = MetadataFile(original_audio_filenames=filenames)

        # Use first file for date generation if no bundle_name provided
        if not bundle_name:
            bundle_name = cls.generate_generic_bundle_name(
                source_audios[0],
                filenames[0],
                config,
            )

        if fs_service is None:
            fs_service = RealFileSystemService()

        return cls(
            bundle_name=bundle_name,
            metadata=metadata,
            source_audios=source_audios,
            fs_service=fs_service,
            config=config,
        )

    def cleanup_inconsistencies(
        self,
        dry_run: bool,
    ) -> None:
        """Cleanup known existing bundle inconsistencies. Must be called explictily.

        Will remove summary and/or commands (both in this instance and in files) if no transcript file exists.
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

    @staticmethod
    def _sort_audio_files_chronologically(
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
        self.metadata.bundle_name_generated = False
        if not dry_run:
            self.metadata.write(self.get_bundle_dir(), self.fs_service)
            if self.transcript:
                self.transcript.unlink(self.get_bundle_dir(), self.fs_service)
            if self.summary:
                self.summary.unlink(self.get_bundle_dir(), self.fs_service)

        self.transcript = None
        self.summary = None

    @staticmethod
    def get_date_for_filename(
        audio_path: Path | None,
        audio_filename: str,
        config: TranscribeConfig,
    ) -> datetime:
        """Try to extract date from filename first, or else from file modified date."""
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
            raise ValueError(msg)

    @staticmethod
    def generate_bundle_name_date_prefix(
        audio_path: Path | None,
        audio_filename: str,
        config: TranscribeConfig,
    ) -> str:
        """Return a date prefix string in the 'YYYY-MM-DD' format."""
        date_from_filename = TranscribeBundle.get_date_for_filename(
            audio_path,
            audio_filename,
            config,
        )
        return date_from_filename.strftime("%Y-%m-%d")

    @classmethod
    def generate_generic_bundle_name(
        cls,
        audio_path: Path | None,
        audio_filename: str,
        config: TranscribeConfig,
    ) -> str:
        """Generate a bundle name based on date and audio filename."""
        logger.debug(f"Generating bundle name for audio file: [{audio_path}]")

        prefix = cls.generate_bundle_name_date_prefix(
            audio_path,
            audio_filename,
            config,
        )
        return f"{prefix}_{Path(audio_filename).stem}"

    def audio_source_needs_removal(
        self,
    ) -> bool:
        """Check if any bundle audio file is older than given days.

        The date is either the file modification date or the bundle date, whichever is latest.
        """
        tz = self.config.general.timezone
        if (
            not self.source_audios
            or self.metadata.keep_forever
            or self.config.general.delete_source_audio_after_days <= 0
        ):
            return False

        bundle_date = self.get_date_from_bundle_name()
        bundle_days_since = get_days_since_time(bundle_date)
        file_days_since = 0

        # Check all files, use the oldest one (max days_since)
        for audio_path in self.source_audios:
            file_date = get_file_modified_date(audio_path, tz)
            file_days_since_current = get_days_since_time(file_date)
            file_days_since = max(file_days_since, file_days_since_current)

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

    @staticmethod
    def gather_existing_bundles(
        output_dir: Path,
        dry_run: bool,
        config: TranscribeConfig,
        fs_service: FileSystemService,
    ) -> list["TranscribeBundle"]:
        """Find and load all bundles from output_dir."""
        bundles: list[TranscribeBundle] = []
        for dir_path in fs_service.list_directory(output_dir):
            if fs_service.directory_exists(dir_path):
                try:
                    bundle = TranscribeBundle.from_existing_directory(
                        dir_path,
                        dry_run,
                        config,
                        fs_service,
                    )
                    bundle.cleanup_inconsistencies(dry_run)
                    bundles.append(bundle)
                except ValueError as e:
                    logger.error(
                        f"Skipping invalid transcribe bundle {dir_path}, exception {e}",
                    )

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
        self.metadata.transcript_model_used = self.config.audio.model
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

    def set_and_write_commands(
        self,
        commands: list[str],
    ) -> None:
        """Set and write the commands to memory and disk."""
        self.metadata.write(self.get_bundle_dir(), self.fs_service)
        # join commands list into a single string
        # with each command on a new line
        commands_str = "\n".join(commands)
        self.commands = CommandsFile(commands_str)
        self.commands.write(self.get_bundle_dir(), self.fs_service)

    def init_metadata(
        self,
        filenames: list[str],
        audio_lengths: list[float],
    ) -> None:
        """Initialize metadata with multiple files."""
        self.metadata.original_audio_filenames = filenames
        # Store total length
        self.metadata.audio_length = sum(audio_lengths)
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

        prefix = self.generate_bundle_name_date_prefix(
            self.source_audios[0] if self.source_audios else None,
            self.metadata.original_audio_filenames[0]
            if self.metadata.original_audio_filenames
            else "",
            self.config,
        )
        new_bundle_name = f"{prefix} {bundle_name_summary}"

        bundle_path_to = self.config.general.store_dir / new_bundle_name
        self.fs_service.rename_directory(bundle_path_from, bundle_path_to)
        self.bundle_name = new_bundle_name

        self.metadata.bundle_name_generated = True
        self.metadata.write(self.get_bundle_dir(), self.fs_service)
