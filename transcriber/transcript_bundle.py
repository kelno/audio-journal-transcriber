from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import shutil
import os

from transcriber.utils import extract_date_from_recording_filename
from transcriber.utils import ensure_directory_exists
from transcriber.logger import get_logger
from transcriber.config import TranscribeConfig

logger = get_logger()

@dataclass
class TranscriptBundle:
    config: TranscribeConfig
    source_audio: Path
    transcript: str
    ai_summary: str
    bundle_name: str|None = None # Can be provided, otherwise will be generated. This will be the bundle directory name.

    # @classmethod
    # def from_existing_directory(cls, _existing_dir: Path):
    #     """Create a ResultFile instance from an existing result file."""

    #     transcript = "dummy transcript"  # Replace with actual reading logic
    #     ai_summary = "dummy AI summary"  # Replace with actual reading logic
    #     return cls("dummy", transcript, ai_summary)

    def get_transcript_file_properties(self) -> str:
        """Generate Obsidian frontmatter properties for the transcript bundle."""

        model = self.config.audio.model
        props = (
            f"---\n"
            f"original_audio_filename: {self.source_audio.name}\n"
            f"model: {model}\n"
            f"---\n"
        )
        return props
    
    def get_summary_file_properties(self) -> str:
        """Generate Obsidian frontmatter properties for the summary file."""

        model = self.config.text.model
        props = (
            f"---\n"
            f"model: {model}\n"
            f"---\n"
        )
        return props

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
            self.bundle_name = self.generate_bundle_name(self.source_audio)
        return self.bundle_name

    def generate_bundle_name(self, audio_path: Path) -> str:
        """Generate output filenames with date prefix if needed."""

        logger.debug(f"Generating bundle name for audio file: [{audio_path}]")

        date_from_filename = self.get_date_for_filename(audio_path)
        prefix = date_from_filename.strftime("%Y-%m-%d")
        return f"{prefix}_{audio_path.stem}"

    def write_file(self, file_path: Path, content: str, props: str):
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(props)
            f.write("\n")
            f.write(content)
        logger.debug(f"Wrote {file_path}")

    def write(self, output_dir: Path, dry_run: bool = False):
        """Write content to the result file."""

        bundle_name = self.get_bundle_name()
        logger.info(f"Writing bundle: [{self.bundle_name}]")
        
        if dry_run:
            return
    
        bundle_dir = output_dir / bundle_name
        ensure_directory_exists(bundle_dir)

        final_audio_path = bundle_dir / f"recording{self.source_audio.suffix}"
        logger.debug(f"Moving audio file from [{self.source_audio}] to [{final_audio_path}]")
        shutil.move(self.source_audio, final_audio_path)

        transcript_path = bundle_dir / "transcript.md"
        self.write_file(transcript_path, self.transcript, self.get_transcript_file_properties())

        summary_path = bundle_dir / "summary.md"
        self.write_file(summary_path, self.ai_summary, self.get_summary_file_properties())
        