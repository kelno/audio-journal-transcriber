import traceback
from dataclasses import dataclass
from pathlib import Path

from transcriber.ai_manager import AIManager
from transcriber.audio_manipulation import AudioManipulation
from transcriber.config import get_config
from transcriber.exception import AudioTranscriberException, TooShortException
from transcriber.globals import is_handled_audio_file
from transcriber.transcribe_bundle import TranscribeBundle
from transcriber.transcribe_bundle_job import BundleJobs, gather_bundle_jobs
from transcriber.utils import ensure_directory_exists, remove_empty_subdirs

from .logger import logger


@dataclass
class AudioTranscriber:
    """Main class for transcribing audio files."""

    dry_run: bool
    ai_manager: AIManager

    def __post_init__(self) -> None:
        """Initialize the audio transcriber."""
        if self.dry_run:
            logger.warning("!!! DRY RUN MODE !!!")
        logger.info(
            f"{type(self).__name__} initialized with\n"
            f"General configuration: {get_config().general}",
        )

    def process_jobs(self, all_jobs_bundles: list[BundleJobs]) -> list[BundleJobs]:
        """Process audio files from the pending directory.

        Args:
            all_jobs_bundles: List of bundle jobs to process.

        Returns:
            list[BundleJobs]: Remaining unprocessed bundle jobs.

        """
        unprocessed_bundles = list[BundleJobs]()
        for jobs_bundle in all_jobs_bundles:
            remaining_jobs_in_bundle = jobs_bundle.copy()
            for job in jobs_bundle:
                try:
                    logger.info(f"Processing job: {job}")
                    job.run(self.ai_manager)
                    # Remove job from jobs bundle on successful execution
                    remaining_jobs_in_bundle.remove(job)
                except TooShortException as e:
                    logger.warning(
                        f"Refused to create bundle from audio file {e.source_audio}: {e}",
                    )
                    if get_config().general.remove_short_files and not self.dry_run:
                        logger.info(f"Removing too short audio file: {e.source_audio}")
                        e.source_audio.unlink()
                    break  # skip remaining jobs in this bundle
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.error(
                        f"Error processing [{job}] (skipping any remaining jobs for this bundle). {traceback.format_exc()}",
                    )
                    if len(remaining_jobs_in_bundle) > 0:
                        unprocessed_bundles.append(remaining_jobs_in_bundle)
                    break  # skip remaining jobs in this bundle

        return unprocessed_bundles

    def log_section_header(self, message: str) -> None:
        """Log a section header with separators."""
        logger.info(f"========== {message} ==========")

    def validate_environment(self) -> None:
        """Raise if missing any environment requirements."""
        input_dir = get_config().general.input_dir
        if not input_dir.exists():
            msg = f"Input directory does not exist: {input_dir}"
            raise AudioTranscriberException(msg)

        if not AudioManipulation.validate_ffmpeg():
            msg = f"ffmpeg is not available in path: {input_dir}"
            raise AudioTranscriberException(msg)

    def gather_pending_audio_files(self, input_dir: Path) -> list[TranscribeBundle]:
        """Import audio files from the input directory as TranscriptBundle instances."""
        bundles = []

        for path in input_dir.rglob("*"):
            if path.is_file() and is_handled_audio_file(path.suffix):
                logger.debug(f"Found audio file: [{path}]")
                bundle = TranscribeBundle.from_audio_file(source_audio=path)
                bundles.append(bundle)

        logger.debug(f"Imported {len(bundles)} audio files as bundles")
        return bundles

    def gather_jobs(self, input_dir: Path) -> list[BundleJobs]:
        """Find audio files in the given directory and its subdirectories.

        Returns a BundleJobs for each audio file found in pending directory,
        and for each bundle found in the managed store directory.
        """
        if not input_dir.exists():
            msg = f"Input directory does not exist: {input_dir}"
            raise FileNotFoundError(msg)

        logger.debug(f"Looking for pending jobs in {input_dir}")

        logger.info(f"Gathering audio files from input directory: {input_dir}")
        bundles = self.gather_pending_audio_files(input_dir)
        store_dir = get_config().general.store_dir
        logger.info(f"Gathering bundles from managed store directory:  {store_dir}")
        bundles.extend(
            TranscribeBundle.gather_existing_bundles(store_dir, self.dry_run)
        )

        jobs: list[BundleJobs] = [
            bundle_jobs
            for bundle in bundles
            if (bundle_jobs := gather_bundle_jobs(bundle, store_dir, self.dry_run))
        ]

        return jobs

    def run(self) -> list[BundleJobs]:
        """Process all files.

        Returns unprocessed jobs as BundleJobs
        """
        self.validate_environment()  # will raise on error

        input_dir = get_config().general.input_dir
        store_dir = get_config().general.store_dir

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
