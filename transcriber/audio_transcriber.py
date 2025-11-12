from dataclasses import dataclass
from pathlib import Path
import os
from datetime import datetime

from transcriber.ai_manager import AIManager
from transcriber.config import TranscribeConfig
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
            f"Cleanup {self.config.general.cleanup}\n"
            f"Text summary {"enabled" if self.config.text.summary_enabled else "disabled"}"
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

    def log_section_header(self, message):
        """Log a section header with separators."""
        logger.info(f"========== {message} ==========")

    @staticmethod
    def cleanup_audio_files_older_than(output_dir: Path, days: int, dry_run: bool = False):
        """
        Clean up audio files that were processed more than X days ago.
        Only removes audio files that have a matching .md file next to them.
        Handles nested directory structure.
        """

        logger.info(f"Starting cleanup of audio files older than {days} days")
        logger.debug(f"Scanning directory for cleanup: {output_dir}")

        current_time = datetime.now().timestamp()
        files_removed = 0
        files_checked = 0

        for file_path in Path(output_dir).rglob("*"):
            if file_path.is_file():
                if not is_handled_audio_file(file_path.suffix):
                    continue

                files_checked += 1

                # Check for matching .md file
                transcript_path = file_path.parent / "summary.md"

                if not transcript_path.exists():
                    logger.warning(f"Skipping [{file_path.name}], as no matching text file was found. ")
                    continue

                mtime = file_path.stat().st_mtime
                age_in_days = (current_time - mtime) / (24 * 3600)

                logger.debug(f'Checking: "{file_path}". ' f"Modified: {datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')}. " f"Age: {int(age_in_days)} days.")

                if age_in_days > days:
                    logger.info(f"Removing file: {file_path}")
                    if not dry_run:
                        file_path.unlink()
                    files_removed += 1
                else:
                    logger.debug("Keeping file (not old enough)")

        logger.info("Cleanup summary:")
        logger.info(f"  Files checked: {files_checked}")
        logger.info(f"  Files removed: {files_removed}")

    def gather_jobs(self, input_dir: Path, output_dir: Path) -> list[BundleJobs]:
        """
        Find audio files in the given subdirectory and its subdirectories.
        Returns a list of Path objects for each audio file found.
        """
        if not input_dir.exists():
            raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

        logger.debug(f"Looking for pending jobs in {input_dir}")

        logger.info(f"Gathering audio files from input directory: {input_dir}")
        bundles = TranscribeBundle.gather_pending_audio_files(input_dir)
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

        # Clean old audio files
        if self.config.general.cleanup != 0:
            self.log_section_header("Cleanup old audio files")
            self.cleanup_audio_files_older_than(self.store_dir, self.config.general.cleanup, self.dry_run)

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
