from dataclasses import dataclass
from pathlib import Path
from abc import ABC, abstractmethod
import shutil
from typing import List

from transcriber.ai_manager import AIManager
from transcriber.logger import get_logger
from transcriber.transcribe_bundle import TranscribeBundle
from transcriber.utils import file_is_in_directory_tree
from transcriber.utils import ensure_directory_exists

logger = get_logger()

@dataclass
class TranscribeJob(ABC):
    """Abstract base class for all job types."""
    bundle: TranscribeBundle
    dry_run: bool

    @abstractmethod
    def run(self, output_base_dir: Path, ai_manager: AIManager):
        """Perform the job's main work."""

    def __str__(self):
        return f"{self.__class__.__name__}({self.bundle.source_audio.name})"

@dataclass
class CreateBundleJob(TranscribeJob):
    """That's "move audio file into its bundle directory" job."""

    def run(self, output_base_dir: Path, ai_manager: AIManager):
        # Implement logic to create an audio bundle
        print(f"Bundling {self.bundle.source_audio} into {output_base_dir}")
        final_audio_path = self.bundle.get_bundle_audio_path(output_base_dir)
        if self.bundle.source_audio != final_audio_path:
            logger.info(f"Moving audio file from [{self.bundle.source_audio}] to [{final_audio_path}]")
            if not self.dry_run:
                shutil.move(self.bundle.source_audio, final_audio_path)
                self.bundle.update_audio_path(final_audio_path)

@dataclass
class TranscriptionJob(TranscribeJob):

    def run(self, output_base_dir: Path, ai_manager: AIManager):
        transcript_path = self.bundle.get_transcript_path(output_base_dir)
        print(f"Transcribing {self.bundle.source_audio} → {transcript_path}")
        raise NotImplementedError("TranscriptionJob not yet implemented.")


@dataclass
class AISummaryJob(TranscribeJob):

    def run(self, output_base_dir: Path, ai_manager: AIManager):
        summary_path = self.bundle.get_summary_path(output_base_dir)
        logger.debug(f"Summarizing {self.bundle.get_bundle_name()} → {summary_path}")
        raise NotImplementedError("AI summary generation not yet implemented.")

class BundleNameJob(TranscribeJob):

    def run(self, output_base_dir: Path, ai_manager: AIManager):
        raise NotImplementedError("BundleNameJob not yet implemented.")

# Moved here to avoid circular imports
def gather_bundle_jobs(bundle: TranscribeBundle, output_dir: Path, do_summary: bool, dry_run: bool) -> List[TranscribeJob]:
    """Gather transcription jobs from this bundle."""
    jobs = []

    bundle_name = bundle.get_bundle_name()
    logger.debug(f"Gathering jobs for bundle: [{bundle_name}]")

    ensure_directory_exists(bundle.get_bundle_dir(output_dir))

    if bundle.source_audio and not file_is_in_directory_tree(output_dir, bundle.source_audio):
        job = CreateBundleJob(bundle, dry_run)
        jobs.append(job)

    if not bundle.transcript:
        job = TranscriptionJob(bundle, dry_run)
        jobs.append(job)

    if do_summary:
        if not bundle.ai_summary:
            job = AISummaryJob(bundle, dry_run)
            jobs.append(job)

        # always needs to be done after summary
        if bundle.needs_naming():
            job = BundleNameJob(bundle, dry_run)
            jobs.append(job)

    return jobs
