from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
from typing import List

from transcriber.globals import is_handled_audio_file
from transcriber.transcribe_bundle_text_file import TranscribeTextFile
from transcriber.utils import extract_date_from_recording_filename
from transcriber.logger import get_logger

logger = get_logger()

TRANSCRIPT_NAME = "transcript.md"
SUMMARY_NAME = "summary.md"

@dataclass
class TranscribeBundle:
    source_audio: Path|None
    transcript: TranscribeTextFile|None
    summary: TranscribeTextFile|None
    bundle_name: str|None # Can be provided, otherwise will be generated. This will be the bundle directory name.

    @classmethod
    def from_existing_directory(cls, existing_dir: Path) -> "TranscribeBundle":
        """
        Create a TranscribeBundle instance from an existing directory.
        
        Can throw ValueError
        """

        bundle = TranscribeBundle(None, None, None, bundle_name=existing_dir.name)
        for file_path in existing_dir.glob('*'):
            if is_handled_audio_file(file_path.name):
                if bundle.source_audio:
                    raise ValueError("Multiple audio files found in bundle") # not yet supported
                bundle.source_audio = file_path
            elif file_path.name == TRANSCRIPT_NAME:
                bundle.transcript = TranscribeTextFile.from_file(file_path)
            elif file_path.name == SUMMARY_NAME:
                bundle.summary = TranscribeTextFile.from_file(file_path)

        if not bundle.source_audio and not bundle.transcript and not bundle.summary:
            raise ValueError("Bundle directory has no valid files")

        return bundle

    @classmethod
    def from_audio_file(cls, source_audio: Path) -> "TranscribeBundle":
        """Create a TranscribeBundle instance from an audio file."""

        return cls(source_audio, transcript = None, summary = None, bundle_name = None)

    def assert_source_audio(self) -> Path:
        if not self.source_audio:
            raise FileNotFoundError("Tried to access non existing source audio")

        return self.source_audio

    def get_file_modified_date(self, audio_path: Path) -> datetime:
        """Get the file's date from its last modified time (format: YYYY-MM-DD).
        Falls back to current date if modification time is unavailable."""

        try:
            file_mtime = os.path.getmtime(audio_path)
            file_date = datetime.fromtimestamp(file_mtime)
            logger.debug(f"Using file last modified date: '{file_date}'")
            return file_date
        except OSError:
            file_date = datetime.now()
            logger.warning(f"Could not get file modification time, using current date: '{file_date}'")
            return file_date

    def get_date_for_filename(self, audio_path: Path) -> datetime:
        date_from_filename = extract_date_from_recording_filename(audio_path)
        if date_from_filename:
            logger.debug(f"Found existing date [{date_from_filename}] in audio filename")
            return date_from_filename
        else:
            file_date = self.get_file_modified_date(audio_path)
            logger.debug(f"No date found in filename, using file modified date : '{file_date}'")
            return file_date

    def get_bundle_name(self) -> str:
        """Get or generate the bundle name."""
        if self.bundle_name is None:
            self.bundle_name = self.generate_bundle_name(self.assert_source_audio())
        return self.bundle_name

    def generate_bundle_name(self, audio_path: Path) -> str:
        """Generate output filenames with date prefix if needed."""

        logger.debug(f"Generating bundle name for audio file: [{audio_path}]")

        date_from_filename = self.get_date_for_filename(audio_path)
        prefix = date_from_filename.strftime("%Y-%m-%d")
        return f"{prefix}_{audio_path.stem}"

    @staticmethod
    def gather_pending_audio_files(source_dir: Path) -> List["TranscribeBundle"]:
        """
        Import audio files from the source directory as TranscriptBundle instances.
        """

        logger.info(f"Importing audio files from source directory: {source_dir}")

        bundles = []
        for path in source_dir.rglob('*'):
            if path.is_file() and is_handled_audio_file(path.name):
                logger.debug(f"Found audio file: [{path}]")
                bundle = TranscribeBundle.from_audio_file(source_audio=path)
                bundles.append(bundle)

        logger.debug(f"Imported {len(bundles)} audio files as bundles")
        return bundles

    @staticmethod
    def gather_existing_bundles(output_dir: Path) -> List["TranscribeBundle"]:
        """Find and load all bundles from output_dir"""

        bundles = []
        for dir_path in output_dir.glob('*'): # no need for recursion
            if dir_path.is_dir():
                try:
                    bundle = TranscribeBundle.from_existing_directory(dir_path)
                    bundles.append(bundle)
                except ValueError as e:
                    logger.error(f"Skipping invalid transcribe bundle {dir_path}, exception {e}")

        logger.debug(f"Found {len(bundles)} existing bundles")
        return bundles

    def needs_naming(self) -> bool:
        """Check if the bundle needs a generated name."""
        return False # Placeholder for future implementation

    def get_bundle_dir(self, output_dir: Path) -> Path:
        """Get the bundle directory path."""
        bundle_name = self.get_bundle_name()
        return output_dir / bundle_name

    def get_transcript_path(self, output_dir: Path) -> Path:
        """Get the transcript file path."""
        bundle_dir = self.get_bundle_dir(output_dir)
        return bundle_dir / TRANSCRIPT_NAME

    def get_summary_path(self, output_dir: Path) -> Path:
        """Get the ai summary file path."""
        bundle_dir = self.get_bundle_dir(output_dir)
        return bundle_dir / SUMMARY_NAME

    def get_bundle_audio_path(self, output_dir: Path) -> Path:
        """Get the audio file path within the bundle dir."""
        bundle_dir = self.get_bundle_dir(output_dir)
        final_audio_path = bundle_dir / self.assert_source_audio().name
        return final_audio_path

    def update_audio_path(self, new_audio_path: Path):
        """Update the source audio path."""
        self.source_audio = new_audio_path
