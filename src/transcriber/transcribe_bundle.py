from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from transcriber.config import get_config
from transcriber.constants import (
    METADATA_FILENAME,
    SUMMARY_FILENAME,
    TRANSCRIPT_FILENAME,
)
from transcriber.text_file import SummaryFile, TextFile, TranscriptFile

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
    bundle_name: str  # directory name is derived from here
    metadata: MetadataFile
    source_audios: list[Path]
    transcript: TextFile | None
    summary: TextFile | None

    @classmethod
    def from_existing_directory(
        cls,
        existing_dir: Path,
        dry_run: bool,
    ) -> "TranscribeBundle":
        """Create a TranscribeBundle instance from an existing already processed directory.

        Args:
            existing_dir: Path to the existing bundle directory.
            dry_run: Whether to simulate operations without making changes.

        Returns:
            TranscribeBundle: The loaded bundle instance.

        Raises:
            ValueError: If the bundle directory is invalid.

        """

        meta_file_path = existing_dir / METADATA_FILENAME
        if not meta_file_path.exists():
            error_msg = "Bundle directory is invalid (no meta file)"
            raise ValueError(error_msg)
        metadata = MetadataFile.from_file(meta_file_path)

        if not metadata:
            error_msg = "Bundle directory is invalid (no audio or meta file)"
            raise ValueError(error_msg)

        bundle_name = existing_dir.name
        source_audios: list[Path] = []
        transcript: TranscriptFile | None = None
        summary: SummaryFile | None = None

        for file_path in existing_dir.glob("*"):
            if is_handled_audio_file(file_path.suffix):
                source_audios.append(file_path)
            elif file_path.name == TRANSCRIPT_FILENAME:
                transcript = TranscriptFile.from_file(file_path)
            elif file_path.name == SUMMARY_FILENAME:
                summary = SummaryFile.from_file(file_path)

        if not source_audios:
            error_msg = "Bundle directory is invalid (no audio files)"
            raise ValueError(error_msg)

        # Sort audio files chronologically
        source_audios = cls._sort_audio_files_chronologically(source_audios)

        # Check if there are new unprocessed audio files
        found_filenames = {f.name for f in source_audios}
        original_filenames = set(metadata.original_audio_filenames)

        bundle = TranscribeBundle(
            bundle_name=bundle_name,
            metadata=metadata,
            source_audios=source_audios,
            transcript=transcript,
            summary=summary,
        )

        if found_filenames != original_filenames:
            logger.info(
                f"Bundle {bundle_name} has new unprocessed audio files. "
                f"Original: {original_filenames}, Found: {found_filenames}"
            )
            bundle.refresh(dry_run)

        return bundle

    @classmethod
    def from_audio_file(cls, source_audio: Path) -> "TranscribeBundle":
        """Create a TranscribeBundle instance from an audio file"""
        metadata = MetadataFile(original_audio_filenames=[source_audio.name])
        bundle_name = TranscribeBundle.generate_generic_bundle_name(
            source_audio, source_audio.name
        )

        return cls(
            bundle_name=bundle_name,
            metadata=metadata,
            source_audios=[source_audio],
            transcript=None,
            summary=None,
        )

    @classmethod
    def from_audio_files(
        cls, source_audios: list[Path], bundle_name: str | None = None
    ) -> "TranscribeBundle":
        """Create a TranscribeBundle from multiple audio files"""
        if not source_audios:
            raise ValueError("Must provide at least one audio file")

        filenames = [f.name for f in source_audios]
        metadata = MetadataFile(original_audio_filenames=filenames)

        # Use first file for date generation if no bundle_name provided
        if not bundle_name:
            bundle_name = cls.generate_generic_bundle_name(
                source_audios[0], filenames[0]
            )

        return cls(
            bundle_name=bundle_name,
            metadata=metadata,
            source_audios=source_audios,
            transcript=None,
            summary=None,
        )

    @staticmethod
    def _sort_audio_files_chronologically(audio_files: list[Path]) -> list[Path]:
        """Sort audio files by extracted filename date, or fallback to modification time."""

        def get_sort_key(file_path: Path) -> tuple:
            # Try to extract date from filename
            filename_date = extract_date_from_recording_filename(file_path.name)
            if filename_date:
                return (0, filename_date)  # (0, date) - use filename date

            # Fallback to file modification time
            mod_date = get_file_modified_date(file_path)
            return (1, mod_date)  # (1, date) - use mod time (secondary sort)

        return sorted(audio_files, key=get_sort_key)

    def assert_source_audios(self) -> list[Path]:
        """Ensure bundle has audio files, raise if empty."""
        if not self.source_audios:
            raise FileNotFoundError("Bundle has no audio files set")
        return self.source_audios

    def refresh(self, dry_run: bool):
        """Clear transcript, summary, and bundle name to mark bundle for reprocessing.
        Used when bundle content changes (e.g., new audio files added).
        """
        self.metadata.bundle_name_generated = False
        if not dry_run:
            self.metadata.write(self.get_bundle_dir())
            if self.transcript:
                self.transcript.unlink(self.get_bundle_dir())
            if self.summary:
                self.summary.unlink(self.get_bundle_dir())

        self.transcript = None
        self.summary = None

    @staticmethod
    def get_date_for_filename(audio_path: Path | None, audio_filename: str) -> datetime:
        """Try to extract date from filename first, or else from file modified date"""
        date_from_filename = extract_date_from_recording_filename(audio_filename)
        if date_from_filename:
            logger.debug(
                f"Found existing date [{date_from_filename}] in audio filename"
            )
            return date_from_filename
        elif audio_path:
            file_date = get_file_modified_date(audio_path)
            logger.debug(
                f"No date found in filename, using file modified date : '{file_date}'"
            )
            return file_date
        else:
            raise ValueError(f"Could not find any date for file {audio_filename}")

    def get_bundle_name(self) -> str:
        """Get or generate the bundle name."""
        if self.bundle_name is None:
            self.bundle_name = self.generate_generic_bundle_name(
                self.source_audios[0] if self.source_audios else None,
                self.metadata.original_audio_filenames[0]
                if self.metadata.original_audio_filenames
                else "",
            )
        return self.bundle_name

    @staticmethod
    def generate_bundle_name_date_prefix(
        audio_path: Path | None, audio_filename: str
    ) -> str:
        """Return a date prefix string in the 'YYYY-MM-DD' format"""
        date_from_filename = TranscribeBundle.get_date_for_filename(
            audio_path, audio_filename
        )
        return date_from_filename.strftime("%Y-%m-%d")

    @classmethod
    def generate_generic_bundle_name(
        cls, audio_path: Path | None, audio_filename: str
    ) -> str:
        """Generate a bundle name based on date and audio filename"""

        logger.debug(f"Generating bundle name for audio file: [{audio_path}]")

        prefix = cls.generate_bundle_name_date_prefix(audio_path, audio_filename)
        return f"{prefix}_{Path(audio_filename).stem}"

    def audio_source_needs_removal(self, config_delete_after_days: int) -> bool:
        """Check if any bundle audio file is older than given days.
        The date is either the file modification date or the bundle date, whichever is latest.
        """
        if (
            not self.source_audios
            or self.metadata.keep_forever
            or config_delete_after_days <= 0
        ):
            return False

        bundle_date = self.get_date_from_bundle_name()
        bundle_days_since = get_days_since_time(bundle_date)
        file_days_since = 0

        # Check all files, use the oldest one (max days_since)
        for audio_path in self.source_audios:
            file_date = get_file_modified_date(audio_path)
            file_days_since_current = get_days_since_time(file_date)
            file_days_since = max(file_days_since, file_days_since_current)

        days_since = min(bundle_days_since, file_days_since)
        return days_since > config_delete_after_days

    def get_date_from_bundle_name(self) -> datetime:
        """Extract date from the bundle name."""
        # Date is in format %Y-%m-%d at the start of the bundle name
        date_str = self.bundle_name[0:10]
        return datetime.strptime(date_str, "%Y-%m-%d")

    @staticmethod
    def gather_existing_bundles(
        output_dir: Path, dry_run: bool
    ) -> list["TranscribeBundle"]:
        """Find and load all bundles from output_dir"""

        bundles = []
        for dir_path in output_dir.glob("*"):  # no need for recursion
            if dir_path.is_dir():
                try:
                    bundle = TranscribeBundle.from_existing_directory(dir_path, dry_run)
                    bundles.append(bundle)
                except ValueError as e:
                    logger.error(
                        f"Skipping invalid transcribe bundle {dir_path}, exception {e}"
                    )

        logger.debug(f"Found {len(bundles)} existing bundles")
        return bundles

    def needs_naming(self) -> bool:
        """Check if the bundle needs a generated name."""
        return not self.metadata.bundle_name_generated

    def get_bundle_dir(self) -> Path:
        """Get the bundle directory path."""
        bundle_name = self.get_bundle_name()
        return get_config().general.store_dir / bundle_name

    def get_bundle_audio_paths(self) -> list[Path]:
        """Get all audio file paths within the bundle dir."""
        bundle_dir = self.get_bundle_dir()
        return [bundle_dir / audio.name for audio in self.source_audios]

    def update_audio_paths(self, new_audio_paths: list[Path] | None):
        """Update the source audio paths."""
        self.source_audios = new_audio_paths or []

    def set_and_write_transcript(self, transcript: str, model_used: str):
        self.metadata.transcript_model_used = model_used
        self.metadata.write(self.get_bundle_dir())
        self.transcript = TranscriptFile(transcript)
        self.transcript.write(self.get_bundle_dir())

    def set_and_write_summary(self, summary: str, model_used: str):
        self.metadata.summary_model_used = model_used
        self.metadata.write(self.get_bundle_dir())
        self.summary = SummaryFile(summary)
        self.summary.write(self.get_bundle_dir())

    def init_metadata(self, filenames: list[str], audio_lengths: list[float]):
        """Initialize metadata with multiple files."""
        self.metadata.original_audio_filenames = filenames
        # Store total length
        self.metadata.audio_length = sum(audio_lengths)
        self.metadata.write(self.get_bundle_dir())

    def set_and_write_bundle_name(self, bundle_name_summary: str):
        """Set bundle name and rename the directory"""
        bundle_path_from = get_config().general.store_dir / self.bundle_name
        if not bundle_path_from.exists():
            raise FileNotFoundError("Bundle directory not found")

        prefix = self.generate_bundle_name_date_prefix(
            self.source_audios[0] if self.source_audios else None,
            self.metadata.original_audio_filenames[0]
            if self.metadata.original_audio_filenames
            else "",
        )
        new_bundle_name = f"{prefix} {bundle_name_summary}"

        bundle_path_to = get_config().general.store_dir / new_bundle_name
        bundle_path_from.rename(bundle_path_to)
        self.bundle_name = new_bundle_name

        self.metadata.bundle_name_generated = True
        self.metadata.write(self.get_bundle_dir())
