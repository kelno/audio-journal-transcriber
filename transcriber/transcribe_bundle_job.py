from dataclasses import dataclass
from pathlib import Path
from abc import ABC, abstractmethod
import shutil
from typing import List

from transcriber.ai_manager import AIManager
from transcriber.config import TranscribeConfig
from transcriber.logger import get_logger
from transcriber.transcribe_bundle import TranscribeBundle, TranscribeBundleTextFile
from transcriber.transcribe_bundle_text_file import TranscribeTextFileProps
from transcriber.utils import file_is_in_directory_tree

logger = get_logger()

@dataclass
class TranscribeBundleJob(ABC):
    """Abstract base class for all job types."""
    bundle: TranscribeBundle
    config: TranscribeConfig
    dry_run: bool

    @abstractmethod
    def run(self, output_base_dir: Path, ai_manager: AIManager):
        """Perform the job's main work."""

    def __str__(self):
        return f"{self.__class__.__name__}({self.bundle.get_bundle_name()})"

@dataclass
class CreateBundleJob(TranscribeBundleJob):
    """That's "move audio file into its bundle directory" job."""

    def run(self, output_base_dir: Path, _ai_manager: AIManager):
        if not self.bundle.source_audio:
            raise FileNotFoundError("Bundle has no audio file set")

        final_audio_path = self.bundle.get_bundle_audio_path(output_base_dir)
        if self.bundle.source_audio != final_audio_path:
            logger.info(f"Moving audio file from [{self.bundle.source_audio}] to [{final_audio_path}]")
            if not self.dry_run:
                shutil.move(self.bundle.source_audio, final_audio_path)
                self.bundle.update_audio_path(final_audio_path)

@dataclass
class TranscriptionJob(TranscribeBundleJob):

    def run(self, output_base_dir: Path, ai_manager: AIManager):
        transcript_path = self.bundle.get_transcript_path(output_base_dir)
        logger.info(f"Transcribing {self.bundle.source_audio} → {transcript_path}")

        if not self.bundle.source_audio:
            raise FileNotFoundError("Bundle has no audio file set")

        if not self.dry_run:
            transcript_content = ai_manager.transcribe_audio(self.bundle.source_audio)
            props = TranscribeTextFileProps(self.config.text.model)
            self.bundle.transcript = TranscribeBundleTextFile(props, transcript_content)
            self.bundle.transcript.write(transcript_path)

@dataclass
class SummaryJob(TranscribeBundleJob):

    def run(self, output_base_dir: Path, ai_manager: AIManager):
        summary_path = self.bundle.get_summary_path(output_base_dir)
        logger.info(f"Summarizing {self.bundle.get_bundle_name()} → {summary_path}")

        if not self.dry_run:
            if not self.bundle.transcript:
                raise ValueError("Cannot generate ai summary without transcript")

            summary_content = ai_manager.try_get_ai_summary(self.bundle.transcript.content)
            props = TranscribeTextFileProps(self.config.text.model)
            self.bundle.summary = TranscribeBundleTextFile(props, summary_content)
            self.bundle.summary.write(summary_path)

class BundleNameJob(TranscribeBundleJob):

    def run(self, output_base_dir: Path, ai_manager: AIManager):
        if not self.bundle.summary:
            raise ValueError("Cannot generate bundle name without AI summary.")

        raise NotImplementedError("BundleNameJob not yet implemented.")

# Moved here to avoid circular imports
def gather_bundle_jobs(bundle: TranscribeBundle, output_dir: Path, config: TranscribeConfig, dry_run: bool) -> List[TranscribeBundleJob]:
    """Gather transcription jobs from this bundle."""
    jobs = []

    bundle_name = bundle.get_bundle_name()
    logger.debug(f"Gathering jobs for bundle: [{bundle_name}]")

    if bundle.source_audio and not file_is_in_directory_tree(bundle.source_audio, output_dir):
        job = CreateBundleJob(bundle, config, dry_run)
        jobs.append(job)

    if not bundle.transcript:
        job = TranscriptionJob(bundle, config, dry_run)
        jobs.append(job)

    if config.text.summary_enabled:
        # always needs to be done after transcription
        if not bundle.summary:
            job = SummaryJob(bundle, config, dry_run)
            jobs.append(job)

        # always needs to be done after summary as this relies on summary content
        if bundle.needs_naming():
            job = BundleNameJob(bundle, config, dry_run)
            jobs.append(job)

    return jobs
