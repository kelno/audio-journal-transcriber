from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

import yaml

from transcriber.globals import is_handled_audio_file
from transcriber.utils import (
    extract_date_from_recording_filename,
    get_file_modified_date,
)
from transcriber.logger import get_logger

logger = get_logger()

TRANSCRIPT_NAME = "transcript.md"
SUMMARY_NAME = "summary.md"
METADATA_NAME = "_metadata.md"


@dataclass
class Metadata:
    """A bundle metadata is kept in this single database file as yaml data"""

    original_audio_filename: str
    transcript_model_used: str | None = None
    summary_model_used: str | None = None
    bundle_name_generated: bool = False
    # add force keep?

    @staticmethod
    def _split_frontmatter(text: str) -> str | None:
        """
        Returns (frontmatter_yaml, body_text)
        """
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 2:
                _, front, _body = parts
                return front.strip()
        return None

    @classmethod
    def from_file(cls, meta_file: Path) -> "Metadata":
        text = meta_file.read_text(encoding="utf-8")
        if front := cls._split_frontmatter(text):
            data = yaml.safe_load(front)
        else:
            raise ValueError(f"Invalid metadata file {meta_file}, failed to find frontmatter")
        return cls(**data)

    def write(self, output_file: Path):
        yaml_text = f"---\n{yaml.safe_dump(asdict(self), sort_keys=False).strip()}\n---\n"
        output_file.write_text(yaml_text, encoding="utf-8")


@dataclass
class TranscribeBundle:

    bundle_name: str  # directory name is derived from here
    metadata: Metadata
    source_audio: Path | None
    transcript: str | None
    summary: str | None

    @classmethod
    def from_existing_directory(cls, existing_dir: Path) -> "TranscribeBundle":
        """
        Create a TranscribeBundle instance from an existing already processed directory.

        Can throw ValueError
        """

        meta_file = existing_dir / METADATA_NAME
        if not meta_file.exists():
            raise ValueError("Bundle directory is invalid (no meta file")
        metadata = Metadata.from_file(meta_file)

        bundle_name = existing_dir.name
        source_audio: Path | None = None
        transcript: str | None = None
        summary: str | None = None

        for file_path in existing_dir.glob("*"):
            if is_handled_audio_file(file_path.suffix):
                if source_audio:
                    raise ValueError("Multiple audio files found in bundle")  # not yet supported
                source_audio = file_path
            elif file_path.name == TRANSCRIPT_NAME:
                transcript = file_path.read_text(encoding="utf-8")
            elif file_path.name == SUMMARY_NAME:
                summary = file_path.read_text(encoding="utf-8")

        if not metadata:
            raise ValueError("Bundle directory is invalid (no audio or meta file")

        return TranscribeBundle(
            bundle_name=bundle_name,
            metadata=metadata,
            source_audio=source_audio,
            transcript=transcript,
            summary=summary,
        )

    @classmethod
    def from_audio_file(cls, source_audio: Path) -> "TranscribeBundle":
        """Create a TranscribeBundle instance from an audio file."""

        metadata = Metadata(original_audio_filename=source_audio.name)
        bundle_name = TranscribeBundle.generate_dumb_bundle_name(source_audio, source_audio.name)

        return cls(
            bundle_name=bundle_name,
            metadata=metadata,
            source_audio=source_audio,
            transcript=None,
            summary=None,
        )

    def assert_source_audio(self) -> Path:
        if not self.source_audio:
            raise FileNotFoundError("Tried to access non existing source audio")

        return self.source_audio

    @staticmethod
    def get_date_for_filename(audio_path: Path | None, audio_filename: str) -> datetime:
        """Try to extract date from filename first, or else from file modified date"""
        date_from_filename = extract_date_from_recording_filename(audio_filename)
        if date_from_filename:
            logger.debug(f"Found existing date [{date_from_filename}] in audio filename")
            return date_from_filename
        elif audio_path:
            file_date = get_file_modified_date(audio_path)
            logger.debug(f"No date found in filename, using file modified date : '{file_date}'")
            return file_date
        else:
            raise ValueError(f"Could not find any date for file {audio_filename}")

    def get_bundle_name(self) -> str:
        """Get or generate the bundle name."""
        if self.bundle_name is None:
            self.bundle_name = self.generate_dumb_bundle_name(self.source_audio, self.metadata.original_audio_filename)
        return self.bundle_name

    @staticmethod
    def generate_bundle_name_prefix(audio_path: Path | None, audio_filename: str) -> str:
        """Return a date prefix string in the 'YYYY-MM-DD' format"""
        date_from_filename = TranscribeBundle.get_date_for_filename(audio_path, audio_filename)
        return date_from_filename.strftime("%Y-%m-%d")

    @classmethod
    def generate_dumb_bundle_name(cls, audio_path: Path | None, audio_filename: str) -> str:
        """Generate a bundle name based on date and audio filename"""

        logger.debug(f"Generating bundle name for audio file: [{audio_path}]")

        prefix = cls.generate_bundle_name_prefix(audio_path, audio_filename)
        return f"{prefix}_{Path(audio_filename).stem}"

    @staticmethod
    def gather_pending_audio_files(input_dir: Path) -> list["TranscribeBundle"]:
        """
        Import audio files from the input directory as TranscriptBundle instances.
        """

        bundles = []

        for path in input_dir.rglob("*"):
            if path.is_file() and is_handled_audio_file(path.suffix):
                logger.debug(f"Found audio file: [{path}]")
                bundle = TranscribeBundle.from_audio_file(source_audio=path)
                bundles.append(bundle)

        logger.debug(f"Imported {len(bundles)} audio files as bundles")
        return bundles

    @staticmethod
    def gather_existing_bundles(output_dir: Path) -> list["TranscribeBundle"]:
        """Find and load all bundles from output_dir"""

        bundles = []
        for dir_path in output_dir.glob("*"):  # no need for recursion
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
        return not self.metadata.bundle_name_generated

    def get_bundle_dir(self, output_base_dir: Path) -> Path:
        """Get the bundle directory path."""
        bundle_name = self.get_bundle_name()
        return output_base_dir / bundle_name

    def get_transcript_path(self, output_base_dir: Path) -> Path:
        """Get the transcript file path."""
        bundle_dir = self.get_bundle_dir(output_base_dir)
        return bundle_dir / TRANSCRIPT_NAME

    def get_summary_path(self, output_base_dir: Path) -> Path:
        """Get the ai summary file path."""
        bundle_dir = self.get_bundle_dir(output_base_dir)
        return bundle_dir / SUMMARY_NAME

    def get_bundle_audio_path(self, output_base_dir: Path) -> Path:
        """Get the audio file path within the bundle dir."""
        bundle_dir = self.get_bundle_dir(output_base_dir)
        final_audio_path = bundle_dir / self.assert_source_audio().name
        return final_audio_path

    def get_meta_file_path(self, output_base_dir: Path) -> Path:
        bundle_dir = self.get_bundle_dir(output_base_dir)
        return bundle_dir / METADATA_NAME

    def update_audio_path(self, new_audio_path: Path):
        """Update the source audio path."""
        self.source_audio = new_audio_path

    def set_and_write_transcript(self, output_base_dir: Path, transcript: str, model_used: str):
        self.metadata.transcript_model_used = model_used
        self.write_metadata(output_base_dir)
        self.transcript = transcript
        transcript_path = self.get_transcript_path(output_base_dir)
        transcript_path.write_text(transcript, encoding="utf-8")

    def set_and_write_summary(self, output_base_dir: Path, summary: str, model_used: str):
        self.metadata.summary_model_used = model_used
        self.write_metadata(output_base_dir)
        self.summary = summary
        summary_path = self.get_summary_path(output_base_dir)
        summary_path.write_text(summary, encoding="utf-8")

    def set_and_write_original_audio_filename(self, output_base_dir: Path, filename: str):
        self.metadata.original_audio_filename = filename
        self.write_metadata(output_base_dir)

    def set_and_write_bundle_name(self, output_base_dir: Path, bundle_name_summary: str):
        """Set bundle name and rename the directory"""
        bundle_path_from = output_base_dir / self.bundle_name
        if not bundle_path_from.exists():
            raise FileNotFoundError("Bundle directory not found")

        prefix = self.generate_bundle_name_prefix(self.source_audio, self.metadata.original_audio_filename)
        new_bundle_name = f"{prefix} {bundle_name_summary}"

        bundle_path_to = output_base_dir / new_bundle_name
        bundle_path_from.rename(bundle_path_to)
        self.bundle_name = new_bundle_name

        self.metadata.bundle_name_generated = True
        self.write_metadata(output_base_dir)

    def write_metadata(self, output_base_dir: Path):
        file_path = self.get_meta_file_path(output_base_dir)
        self.metadata.write(file_path)
