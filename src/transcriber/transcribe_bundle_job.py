from dataclasses import dataclass
from pathlib import Path
from abc import ABC, abstractmethod
import shutil

from .ai_manager import AIManager
from .config import TranscribeConfig
from .exception import EmptyTranscriptException
from .logger import logger
from .transcribe_bundle import TranscribeBundle
from .utils import ensure_directory_exists, file_is_in_directory_tree


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


# Job list for a single bundle
type BundleJobs = list[TranscribeBundleJob]


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
                ensure_directory_exists(final_audio_path.parent)
                shutil.move(self.bundle.source_audio, final_audio_path)
                self.bundle.update_audio_path(final_audio_path)
                self.bundle.set_and_write_original_audio_filename(output_base_dir, final_audio_path.name)


@dataclass
class TranscriptionJob(TranscribeBundleJob):

    def run(self, output_base_dir: Path, ai_manager: AIManager):
        transcript_path = self.bundle.get_transcript_path(output_base_dir)
        logger.info(f"Transcribing {self.bundle.source_audio} → {transcript_path}")

        if not self.bundle.source_audio:
            raise FileNotFoundError(f"{self}: Bundle has no audio file set")

        if not self.dry_run:
            transcript_content = ai_manager.transcribe_audio(self.bundle.source_audio)
            if transcript_content.strip() == "":
                raise EmptyTranscriptException(f"{self}: Transcription resulted in empty transcript")

            self.bundle.set_and_write_transcript(output_base_dir, transcript_content, self.config.audio.model)


@dataclass
class SummaryJob(TranscribeBundleJob):
    """Generate AI summary for the bundle based on transcript."""

    def run(self, output_base_dir: Path, ai_manager: AIManager):
        summary_path = self.bundle.get_summary_path(output_base_dir)
        logger.info(f"Summarizing {self.bundle.get_bundle_name()} → {summary_path}")

        if self.dry_run:
            return

        if not self.bundle.transcript:
            raise ValueError(f"{self}: Cannot generate ai summary without transcript")

        summary_content = ai_manager.get_ai_summary(self.bundle.transcript)
        logger.info(f"Summary complete: {summary_content[:40]}")
        self.bundle.set_and_write_summary(output_base_dir, summary_content, self.config.text.model)


@dataclass
class BundleNameJob(TranscribeBundleJob):
    """Generate AI-based bundle name based on summary."""

    def run(self, output_base_dir: Path, ai_manager: AIManager):
        logger.info(f"Generating buddle name for {self.bundle.get_bundle_name()}")
        if self.dry_run:
            return

        if not self.bundle.summary:
            raise ValueError("Cannot generate bundle name without AI summary.")

        try:
            bundle_name = ai_manager.get_bundle_name_summary(self.bundle.summary)
        except Exception:
            logger.error(f"{self}: Failed to generate bundle name")
            raise

        logger.info(f"Generated bundle name: {bundle_name}")
        self.bundle.set_and_write_bundle_name(output_base_dir, bundle_name)


@dataclass
class DeleteAudioFileJob(TranscribeBundleJob):
    """Remove audio file"""

    def run(self, _output_base_dir: Path, _ai_manager: AIManager):
        if not self.bundle.source_audio:
            raise FileNotFoundError("Bundle has no audio file set")

        logger.info(f"Deleting old audio file: {self.bundle.source_audio}")
        if not self.dry_run:
            self.bundle.source_audio.unlink()
            self.bundle.update_audio_path(None)


# Moved here to avoid circular imports
def gather_bundle_jobs(bundle: TranscribeBundle, store_dir: Path, config: TranscribeConfig, dry_run: bool) -> BundleJobs:
    """Gather transcription jobs from this bundle. Jobs needs to be run in order."""
    jobs = []

    bundle_name = bundle.get_bundle_name()
    logger.debug(f"Gathering jobs for bundle: [{bundle_name}]")

    if bundle.source_audio:
        is_new_audio = not file_is_in_directory_tree(bundle.source_audio, store_dir)
        if is_new_audio:
            job = CreateBundleJob(bundle, config, dry_run)
            jobs.append(job)

        if not is_new_audio and bundle.audio_source_needs_removal(config.general.delete_source_audio_after_days):
            job = DeleteAudioFileJob(bundle, config, dry_run)
            jobs.append(job)
        elif not bundle.transcript:
            job = TranscriptionJob(bundle, config, dry_run)
            jobs.append(job)

    if config.text.summary_enabled:
        if not bundle.summary:
            # First check if transcript exists or TranscriptionJob is scheduled
            transcript_exists_or_scheduled = bundle.transcript is not None or any(isinstance(j, TranscriptionJob) for j in jobs)
            if transcript_exists_or_scheduled:
                job = SummaryJob(bundle, config, dry_run)
                jobs.append(job)

        # always needs to be done after summary as this relies on summary content
        if bundle.needs_naming():
            job = BundleNameJob(bundle, config, dry_run)
            jobs.append(job)

    return jobs
