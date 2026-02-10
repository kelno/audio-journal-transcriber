from dataclasses import dataclass
from pathlib import Path

from transcriber.ai_manager import AIManager
from transcriber.audio_manipulation import AudioManipulation
from transcriber.config import TranscribeConfig
from transcriber.exception import AudioTranscriberException, TooShortException
from transcriber.globals import is_handled_audio_file
from transcriber.transcribe_bundle import TranscribeBundle
from transcriber.logger import get_logger
from transcriber.transcribe_bundle_job import (
    BundleJobs,
    gather_bundle_jobs,
)
from transcriber.utils import ensure_directory_exists, remove_empty_subdirs

logger = get_logger()


@dataclass
class AudioTranscriber:
    config: TranscribeConfig
    store_dir: Path
    dry_run: bool = False

    def __post_init__(self):
        if self.dry_run:
            logger.warning("!!! DRY RUN MODE !!!")
        logger.info(
            f"{type(self).__name__} initialized with\n"
            f"Store directory: {self.store_dir}\n"
            f"Delete source audio after days: {self.config.general.delete_source_audio_after_days}\n"
            f"Text summary {'enabled' if self.config.text.summary_enabled else 'disabled'}"
        )

    def process_jobs(self, all_jobs: list[BundleJobs], output_dir: Path, ai_manager: AIManager):
        """Process audio files from the pending directory."""

        for bundle_jobs in all_jobs:
            try:  # catch at the bundle level rather than job level, we want to skip remaining jobs on failure
                for job in bundle_jobs:
                    logger.info(f"Processing job: {job}")
                    job.run(output_dir, ai_manager)
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
                except TooShortException as e:
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

    def run(self, input_dir: Path):
        ensure_directory_exists(self.store_dir)

        # Make sure obsidian_root exists
        if not input_dir.exists():
            raise ValueError(f"Input directory does not exist: {input_dir}")

        self.log_section_header("Init AI Manager")
        ai_manager = AIManager(self.config)

        self.log_section_header("Gathering Jobs")
        jobs = self.gather_jobs(input_dir, self.store_dir)
        if not jobs:
            logger.info("No jobs found for processing")
        else:
            logger.info(f"Found pending jobs for {len(jobs)} bundles")
            self.log_section_header("Processing Jobs")
            self.process_jobs(jobs, self.store_dir, ai_manager)

        if not self.dry_run:
            remove_empty_subdirs(input_dir)

        self.log_section_header("Summary")
        logger.info("Transcription process finished.")
