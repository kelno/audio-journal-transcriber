import threading
from dataclasses import dataclass, field
from pathlib import Path
import time

from transcriber.file_watcher import FileWatcher

from .ai_manager import AIManager
from .audio_manipulation import AudioManipulation
from .config import TranscribeConfig
from .exception import AudioTranscriberException
from .globals import is_handled_audio_file
from .transcribe_bundle import TranscribeBundle
from .logger import logger
from .transcribe_bundle_job import (
    BundleJobs,
    gather_bundle_jobs,
)
from .utils import ensure_directory_exists, remove_empty_subdirs


@dataclass
class AudioTranscriber:
    config: TranscribeConfig
    dry_run: bool
    ai_manager: AIManager

    def __post_init__(self):
        if self.dry_run:
            logger.warning("!!! DRY RUN MODE !!!")
        logger.info(
            f"{type(self).__name__} initialized with\n"
            f"Store directory: {self.config.general.store_dir}\n"
            f"Delete source audio after days: {self.config.general.delete_source_audio_after_days}\n"
            f"Text summary {'enabled' if self.config.text.summary_enabled else 'disabled'}"
        )

    def process_jobs(self, all_jobs_bundles: list[BundleJobs]) -> list[BundleJobs]:
        """Process audio files from the pending directory.

        Returns remaining unprocessed bundle jobs
        """
        store_dir = self.config.general.store_dir
        unprocessed_bundles = list[BundleJobs]()
        for jobs_bundle in all_jobs_bundles:
            remaining_jobs_in_bundle = jobs_bundle.copy()
            # We want to skip remaining jobs in a bundle on failure and proceed with next bundles
            try:
                for job in jobs_bundle:
                    logger.info(f"Processing job: {job}")
                    job.run(store_dir, self.ai_manager)
                    # Remove job from jobs bundle on successful execution
                    remaining_jobs_in_bundle.remove(job)
            except (OSError, AudioTranscriberException) as e:
                logger.error(f"Error processing [{job}] (skipping any remaining jobs for this bundle). {e}")
                if len(remaining_jobs_in_bundle) > 0:
                    unprocessed_bundles.append(remaining_jobs_in_bundle)

        return unprocessed_bundles

    def log_section_header(self, message):
        """Log a section header with separators."""
        logger.info(f"========== {message} ==========")

    def validate_environment(self):
        """Raise if missing any environment requirements"""

        input_dir = self.config.general.input_dir
        if not input_dir.exists():
            raise AudioTranscriberException(f"Input directory does not exist: {input_dir}")

        if not AudioManipulation.validate_ffmpeg():  # NYI: Check if ffmpeg is available in path
            raise AudioTranscriberException("Could not validate ffmpeg")

    def gather_pending_audio_files(self, input_dir: Path) -> list[TranscribeBundle]:
        """
        Import audio files from the input directory as TranscriptBundle instances.
        """

        bundles = []

        for path in input_dir.rglob("*"):
            if path.is_file() and is_handled_audio_file(path.suffix):
                logger.debug(f"Found audio file: [{path}]")
                try:
                    bundle = TranscribeBundle.from_audio_file(source_audio=path, min_length=self.config.general.min_length_seconds)
                    bundles.append(bundle)
                except AudioTranscriberException as e:
                    logger.warning(f"Failed to create bundle from audio file {path}, exception {e}")
                    if self.config.general.remove_short_files and not self.dry_run:
                        logger.info(f"Removing short audio file: {path}")
                        path.unlink()

        logger.debug(f"Imported {len(bundles)} audio files as bundles")
        return bundles

    def gather_jobs(self, input_dir: Path) -> list[BundleJobs]:
        """
        Find audio files in the given subdirectory and its subdirectories.
        Returns a list of Path objects for each audio file found.
        """
        if not input_dir.exists():
            raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

        logger.debug(f"Looking for pending jobs in {input_dir}")

        logger.info(f"Gathering audio files from input directory: {input_dir}")
        # TODO: SLOW
        bundles = self.gather_pending_audio_files(input_dir)
        store_dir = self.config.general.store_dir
        logger.info(f"Gathering bundles from managed store directory:  {store_dir}")
        bundles.extend(TranscribeBundle.gather_existing_bundles(store_dir))

        jobs: list[BundleJobs] = []

        for bundle in bundles:
            if bundle_jobs := gather_bundle_jobs(bundle, store_dir, self.config, self.dry_run):
                jobs.append(bundle_jobs)

        return jobs

    def run(self):
        """Process all files"""

        self.validate_environment()  # will raise on error

        input_dir = self.config.general.input_dir
        store_dir = self.config.general.store_dir

        # Create store_dir if needed
        ensure_directory_exists(store_dir)

        self.log_section_header("Gathering Jobs")
        jobs = self.gather_jobs(input_dir)
        unprocessed_bundles = list[BundleJobs]()
        if not jobs:
            logger.info("No jobs found for processing")
        else:
            logger.info(f"Found pending jobs for {len(jobs)} bundles")
            self.log_section_header("Processing Jobs")
            unprocessed_bundles = self.process_jobs(jobs)

        if not self.dry_run:
            remove_empty_subdirs(input_dir)

        self.log_section_header("Summary")
        logger.info("Transcription process finished.")

        return unprocessed_bundles
