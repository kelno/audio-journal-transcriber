from dataclasses import dataclass
from pathlib import Path

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

    def process_jobs(self, all_jobs: list[BundleJobs], output_dir: Path):
        """Process audio files from the pending directory."""

        for bundle_jobs in all_jobs:
            try:  # catch at the bundle level rather than job level, we want to skip remaining jobs on failure
                for job in bundle_jobs:
                    logger.info(f"Processing job: {job}")
                    job.run(output_dir, self.ai_manager)
            except OSError as e:
                logger.error(f"Error processing [{job}] (skipping any remaining jobs for this bundle). {e}")
            except AudioTranscriberException as e:
                logger.error(f"Error processing [{job}] (skipping any remaining jobs for this bundle). {e}")

    def log_section_header(self, message):
        """Log a section header with separators."""
        logger.info(f"========== {message} ==========")

    @staticmethod
    def validate_environment() -> bool:
        """Returns wheter environment has the proper requirements"""

        if not AudioManipulation.validate_ffmpeg():  # NYI: Check if ffmpeg is available in path
            logger.error("Could not validate ffmpeg")
            return False

        return True

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

    def gather_jobs(self, input_dir: Path, output_dir: Path) -> list[BundleJobs]:
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
        logger.info(f"Gathering bundles from managed store directory:  {output_dir}")
        bundles.extend(TranscribeBundle.gather_existing_bundles(output_dir))

        jobs: list[BundleJobs] = []

        for bundle in bundles:
            if bundle_jobs := gather_bundle_jobs(bundle, output_dir, self.config, self.dry_run):
                jobs.append(bundle_jobs)

        return jobs

    def process_single_file(self, file_path: Path, output_dir: Path):
        """Process a single audio file."""
        if file_path.is_file() and is_handled_audio_file(file_path.suffix):
            try:
                bundle = TranscribeBundle.from_audio_file(source_audio=file_path, min_length=self.config.general.min_length_seconds)
                if bundle_jobs := gather_bundle_jobs(bundle, output_dir, self.config, self.dry_run):
                    self.process_jobs([bundle_jobs], output_dir)

            except AudioTranscriberException as e:
                logger.error(f"Error processing file {file_path}: {e}")

    def run(self):
        input_dir = self.config.general.input_dir
        store_dir = self.config.general.store_dir
        ensure_directory_exists(store_dir)

        # Make sure obsidian_root exists
        if not input_dir.exists():
            raise ValueError(f"Input directory does not exist: {input_dir}")

        self.log_section_header("Gathering Jobs")
        jobs = self.gather_jobs(input_dir, store_dir)
        if not jobs:
            logger.info("No jobs found for processing")
        else:
            logger.info(f"Found pending jobs for {len(jobs)} bundles")
            self.log_section_header("Processing Jobs")
            self.process_jobs(jobs, store_dir)

        if not self.dry_run:
            remove_empty_subdirs(input_dir)

        self.log_section_header("Summary")
        logger.info("Transcription process finished.")
