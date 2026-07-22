from dataclasses import dataclass
from pathlib import Path

from transcriber.ai_manager import AIManager
from transcriber.audio_service import AudioService, RealAudioService
from transcriber.commands.command_registry import COMMAND_REGISTRY
from transcriber.config import TranscribeConfig
from transcriber.exception import (
    AbortRemainingBundleJobsException,
    AudioTranscriberException,
    MergeBlockedException,
    TooShortException,
)
from transcriber.files.file_system import FileSystemService, RealFileSystemService
from transcriber.globals import is_handled_audio_file
from transcriber.transcribe_bundle import TranscribeBundle
from transcriber.transcribe_bundle_job import (
    BundleJobs,
    BundleNameJob,
    CreateBundleJob,
    DeleteAudioFileJob,
    GatherCommandsJob,
    RunCommandsJob,
    SummaryJob,
    TranscribeBundleJob,
    TranscriptionJob,
)
from transcriber.utils import (
    file_is_in_directory_tree,
    remove_empty_subdirs,
)

from .logger import logger


@dataclass
class AudioTranscriber:
    """Main class for transcribing audio files."""

    dry_run: bool
    only_one_bundle: bool
    ai_manager: AIManager
    config: TranscribeConfig
    fs_service: FileSystemService
    audio_service: AudioService

    def __init__(
        self,
        dry_run: bool,
        ai_manager: AIManager,
        config: TranscribeConfig,
        only_one_bundle: bool = False,
        fs_service: FileSystemService | None = None,
        audio_service: AudioService | None = None,
    ) -> None:
        """Initialize the audio transcriber."""
        self.dry_run = dry_run
        self.only_one_bundle = only_one_bundle
        self.ai_manager = ai_manager
        self.config = config
        self.fs_service = fs_service or RealFileSystemService(config)
        self.audio_service = audio_service or RealAudioService()

    def __post_init__(self) -> None:
        """Initialize the audio transcriber."""
        if self.dry_run:
            logger.warning("!!! DRY RUN MODE !!!")
        logger.info(
            f"{type(self).__name__} initialized with\nGeneral configuration: {self.config.general}",
        )

    def process_jobs(self, all_jobs_bundles: list[BundleJobs]) -> list[BundleJobs]:
        """Process audio files from the pending directory.

        Args:
            all_jobs_bundles: List of bundle jobs to process.

        Returns:
            list[BundleJobs]: Remaining unprocessed bundle jobs.

        """
        # Keep a cache of loaded bundles for the time of this function, to allow subroutines to interact with other bundles
        bundle_cache = {jobs[0].bundle for jobs in all_jobs_bundles}
        unprocessed_bundles = list[BundleJobs]()
        for jobs_bundle in all_jobs_bundles:
            remaining_jobs_in_bundle = jobs_bundle.copy()
            for job in jobs_bundle:
                try:
                    logger.debug(f"Processing job: {job}")
                    job.run(
                        ai_manager=self.ai_manager,
                        config=self.config,
                        bundle_cache=bundle_cache,
                    )
                    # Remove job from jobs bundle on successful execution
                    remaining_jobs_in_bundle.remove(job)
                except TooShortException as e:
                    logger.warning(
                        f"Refused to create bundle from audio file {e.source_audio}: {e}",
                    )
                    if self.config.general.remove_short_files and not self.dry_run:
                        logger.info(f"Removing too short audio file: {e.source_audio}")
                        e.source_audio.unlink()
                    break  # skip remaining jobs in this bundle
                except AbortRemainingBundleJobsException as e:
                    logger.debug(
                        f"Skipping remaining jobs for current bundle, requested by job {job}: {e}",
                    )
                    break  # skip remaining jobs in this bundle
                except MergeBlockedException as e:
                    # A previous merge into the target failed and left a marker. Stop
                    # processing this bundle and do NOT re-enqueue it, so the failed
                    # merge is not retried automatically until the marker is cleared.
                    # This requires human intervention.
                    logger.error(f"Merge blocked, bundle skipped (human action required): {e}")
                    break  # skip remaining jobs in this bundle
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.exception(f"Error processing {job} (skipping any remaining jobs for this bundle).")
                    if len(remaining_jobs_in_bundle) > 0:
                        unprocessed_bundles.append(remaining_jobs_in_bundle)
                    break  # skip remaining jobs in this bundle

        return unprocessed_bundles

    def log_section_header(self, message: str) -> None:
        """Log a section header with separators."""
        logger.info(f"========== {message} ==========")

    def validate_environment(self) -> None:
        """Raise if missing any environment requirements."""
        input_dir = self.config.general.input_dir
        if not self.fs_service.directory_exists(input_dir):
            msg = f"Input directory does not exist: {input_dir}"
            raise AudioTranscriberException(msg)

        if not self.audio_service.validate_ffmpeg():
            msg = f"ffmpeg is not available in path: {input_dir}"
            raise AudioTranscriberException(msg)

    def gather_pending_audio_files(self, input_dir: Path) -> list[TranscribeBundle]:
        """Import audio files from the input directory as TranscriptBundle instances."""
        bundles: list[TranscribeBundle] = []

        for path in input_dir.rglob("*"):
            if path.is_file() and is_handled_audio_file(path.suffix):
                logger.debug(f"Found audio file: [{path}]")
                bundle = TranscribeBundle.from_audio_file(
                    source_audio=path,
                    config=self.config,
                    fs_service=self.fs_service,
                    audio_service=self.audio_service,
                )
                bundles.append(bundle)

        logger.debug(f"Imported {len(bundles)} audio files as bundles")
        return bundles

    def gather_jobs(self, input_dir: Path) -> list[BundleJobs]:
        """Find audio files in the given directory and its subdirectories.

        Returns a BundleJobs for each audio file found in pending directory,
        and for each bundle found in the managed store directory.
        """
        if not self.fs_service.directory_exists(input_dir):
            msg = f"Input directory does not exist: {input_dir}"
            raise FileNotFoundError(msg)

        logger.debug(f"Looking for pending jobs in {input_dir}")

        logger.info(f"Gathering audio files from input directory: {input_dir}")
        bundles = self.gather_pending_audio_files(input_dir)
        store_dir = self.config.general.store_dir
        logger.info(f"Gathering bundles from managed store directory: {store_dir}")
        bundles.extend(
            TranscribeBundle.gather_existing_bundles(
                store_dir=store_dir,
                dry_run=self.dry_run,
                cleanup_bundle=True,
                config=self.config,
                fs_service=self.fs_service,  # type: ignore[arg-type]
                audio_service=self.audio_service,
            ),
        )

        # one BundleJobs per bundle
        jobs: list[BundleJobs] = [
            bundle_jobs
            for bundle in bundles
            if (
                bundle_jobs := self.gather_bundle_jobs(
                    bundle,
                    store_dir,
                    self.dry_run,
                    config=self.config,
                )
            )
        ]
        if self.only_one_bundle:
            jobs = jobs[:1]

        return jobs

    # Moved here to avoid circular imports
    def gather_bundle_jobs(
        self,
        bundle: TranscribeBundle,
        store_dir: Path,
        dry_run: bool,
        config: TranscribeConfig,
    ) -> BundleJobs:
        """Gather transcription jobs from this bundle. Jobs needs to be run in order."""
        logger.debug(f"Gathering jobs for bundle: {bundle}")

        jobs: list[TranscribeBundleJob] = []
        jobs.extend(self._gather_lifecycle_jobs(bundle, store_dir, dry_run))
        jobs.extend(self._gather_command_jobs(bundle, dry_run))
        jobs.extend(self._gather_post_processing_jobs(bundle, jobs, dry_run, config))

        return jobs

    def _gather_lifecycle_jobs(
        self,
        bundle: TranscribeBundle,
        store_dir: Path,
        dry_run: bool,
    ) -> list[TranscribeBundleJob]:
        """Gather bundle creation, deletion, and transcription jobs."""
        if not bundle.source_audios:
            return []

        jobs: list[TranscribeBundleJob] = []
        is_new_audio = not file_is_in_directory_tree(bundle.source_audios[0], store_dir)
        if is_new_audio:
            jobs.append(CreateBundleJob(bundle, dry_run))
        if not bundle.transcript:
            jobs.append(TranscriptionJob(bundle, dry_run))
        if bundle.audio_source_needs_removal(store_dir):
            jobs.append(DeleteAudioFileJob(bundle, dry_run))

        return jobs

    def _gather_command_jobs(
        self,
        bundle: TranscribeBundle,
        dry_run: bool,
    ) -> list[TranscribeBundleJob]:
        """Gather command extraction and execution jobs."""
        if not bundle.source_audios and not bundle.commands:
            return []

        if not bundle.commands:
            return [
                GatherCommandsJob(bundle, dry_run),
                RunCommandsJob(bundle, dry_run),
            ]

        if bundle.commands.has_commands_needing_processing(
            max_attempts_for=lambda cmd_type: COMMAND_REGISTRY[cmd_type].max_attempts,
        ):
            return [RunCommandsJob(bundle, dry_run)]

        return []

    def _gather_post_processing_jobs(
        self,
        bundle: TranscribeBundle,
        existing_jobs: list[TranscribeBundleJob],
        dry_run: bool,
        config: TranscribeConfig,
    ) -> list[TranscribeBundleJob]:
        """Gather summary and naming jobs for the bundle."""
        jobs: list[TranscribeBundleJob] = []

        if not config.text.summary_enabled:
            return jobs

        if self._should_generate_summary(bundle, existing_jobs):
            jobs.append(SummaryJob(bundle, dry_run))

        # always needs to be done after summary as this relies on summary content
        if bundle.needs_naming():
            jobs.append(BundleNameJob(bundle, dry_run))

        return jobs

    def _should_generate_summary(
        self,
        bundle: TranscribeBundle,
        existing_jobs: list[TranscribeBundleJob],
    ) -> bool:
        """Return whether a summary job should be scheduled for the bundle.

        A summary is needed when:
        - no summary exists yet and a transcript is (or will be) available, or
        - a summary already exists but the user-provided context
          (custom_context.md) changed since the summary was generated, detected
          via the persisted summary_context_hash.
        """
        has_transcript = bundle.transcript is not None or any(isinstance(job, TranscriptionJob) for job in existing_jobs)
        if not has_transcript:
            return False
        if bundle.summary is None:
            return True
        # Summary exists: regenerate only if the recording-specific context changed.
        return bundle.metadata.summary_context_hash != bundle.compute_context_hash()

    def run(self) -> list[BundleJobs]:
        """Process all files.

        Returns unprocessed jobs as BundleJobs
        """
        self.validate_environment()  # will raise on error

        input_dir = self.config.general.input_dir
        store_dir = self.config.general.store_dir

        # Create store_dir if needed
        self.fs_service.ensure_directory_exists(store_dir)

        self.log_section_header("Gathering Jobs")
        all_bundle_jobs = self.gather_jobs(input_dir)
        unprocessed_bundles = list[BundleJobs]()
        if not all_bundle_jobs:
            logger.info("No jobs found for processing")
        else:
            total_jobs = sum(len(bundle_jobs) for bundle_jobs in all_bundle_jobs)
            logger.info(f"Found {total_jobs} pending jobs for {len(all_bundle_jobs)} bundles")
            self.log_section_header("Processing Jobs")
            unprocessed_bundles = self.process_jobs(all_bundle_jobs)

        if not self.dry_run:
            remove_empty_subdirs(input_dir)

        self.log_section_header("Summary")
        logger.info("Transcription process finished.")
        if len(unprocessed_bundles) > 0:
            logger.info(f"{len(unprocessed_bundles)} bundle(s) were not fully processed.")
        else:
            logger.info("All bundles were processed successfully.")

        return unprocessed_bundles
