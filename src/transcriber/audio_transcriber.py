from dataclasses import dataclass
from pathlib import Path

from transcriber.actions.filesystem_action_requests import FilesystemActionRequestAdapter
from transcriber.ai_manager import AIManager
from transcriber.audio_service import AudioService, RealAudioService
from transcriber.commands.command_handlers import AbortForMergeTargetException
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
from transcriber.transcribe_bundle import BundleCache, TranscribeBundle
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

# A bundle's work queue: the jobs left to run for that bundle, plus how many
# times it has been re-queued (used to break accidental merge loops).
type BundleJobQueue = "dict[str, BundleJobQueueEntry]"

MAX_REQUEUE_PER_BUNDLE = 5


@dataclass
class BundleJobQueueEntry:
    """One bundle's jobs plus its requeue counter."""

    jobs: BundleJobs
    requeue_count: int = 0


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

    def _handle_process_jobs_exception(self, job: TranscribeBundleJob, e: Exception, queue: BundleJobQueue) -> bool:
        """Handle a job exception and update the ID-keyed work queue.

        Args:
            job: Job whose execution raised the exception.
            e: Exception raised by the job.
            queue: Remaining jobs indexed by persistent bundle ID.

        Returns:
            Whether the exception represents an error for reporting purposes.

        """
        if isinstance(e, TooShortException):
            logger.warning(
                f"Refusing to create bundle from too short audio file {e.source_audio}: {e}",
            )
            if self.config.general.remove_short_files and not self.dry_run:
                logger.info(f"Removing too short audio file: {e.source_audio}")
                self.fs_service.delete_file(e.source_audio)
            return False
        elif isinstance(e, AbortRemainingBundleJobsException):
            logger.debug(
                f"Skipping remaining jobs for current bundle, requested by job {job}: {e}",
            )
            return False
        elif isinstance(e, AbortForMergeTargetException):
            logger.debug(f"Merge finished, re-processing target bundle: {e.bundle.bundle_name}")
            # Drop the target's still-queued (stale) jobs.
            # Re-gather fresh jobs from the merged target
            existing = queue.pop(e.bundle.bundle_id, None)
            requeue_count = (existing.requeue_count + 1) if existing is not None else 1
            target_name = e.bundle.bundle_name
            if requeue_count > MAX_REQUEUE_PER_BUNDLE:
                logger.error(
                    f"Bundle {target_name} re-queued too many times; stopping to avoid a loop.",
                )
                # Then we're done, at this point we've already removed any remaining jobs for the target
                return True

            # Recompute remaining jobs for target
            queue[e.bundle.bundle_id] = BundleJobQueueEntry(
                jobs=self.gather_bundle_jobs(
                    e.bundle,
                    self.config.general.store_dir,
                    self.dry_run,
                    config=self.config,
                ),
                requeue_count=requeue_count,
            )
            return False
        elif isinstance(e, MergeBlockedException):
            # A previous merge into the target failed and left a marker. Stop
            # processing this bundle and do NOT re-enqueue it, so the failed
            # merge is not retried automatically until the marker is cleared.
            # This requires human intervention.
            logger.error(f"Merge blocked, bundle skipped (human action required): {e}")
            return True
        else:
            logger.exception(f"Error processing {job} (skipping any remaining jobs for this bundle).")
            return True

    def process_jobs(self, all_jobs_bundles: list[BundleJobs], bundle_cache: BundleCache) -> list[BundleJobs]:
        """Process audio files from the pending directory.

        Args:
            all_jobs_bundles: List of bundle jobs to process.
            bundle_cache: Bundles indexed by persistent ID for job execution.

        Returns:
            list[BundleJobs]: Remaining unprocessed bundle jobs.

        """
        # Stable IDs keep queue entries addressable while bundle names change.
        queue: BundleJobQueue = {
            jobs_bundle[0].bundle.bundle_id: BundleJobQueueEntry(jobs=jobs_bundle) for jobs_bundle in all_jobs_bundles if jobs_bundle
        }

        errored_bundles = list[BundleJobs]()
        while queue:
            # Process one bundle at a time; popitem() yields an arbitrary bundle.
            _bundle_id, entry = queue.popitem()
            jobs_bundle = entry.jobs
            if not jobs_bundle:
                continue
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
                except Exception as e:  # pylint: disable=broad-exception-caught
                    errored = self._handle_process_jobs_exception(job, e, queue)
                    if errored and len(remaining_jobs_in_bundle) > 0:
                        errored_bundles.append(remaining_jobs_in_bundle)
                    break  # skip remaining jobs in this bundle

        return errored_bundles

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

    def gather_pending_audio_files(self, input_dir: Path) -> BundleCache:
        """Import audio files from the input directory as TranscriptBundle instances.

        Args:
            input_dir: Directory tree to scan for pending audio files.

        Returns:
            Newly discovered bundles indexed by persistent ID.

        """
        bundles: BundleCache = {}

        for path in input_dir.rglob("*"):
            if path.is_file() and is_handled_audio_file(path.suffix):
                logger.debug(f"Found audio file: [{path}]")
                bundle = TranscribeBundle.from_audio_file(
                    source_audio=path,
                    config=self.config,
                    fs_service=self.fs_service,
                    audio_service=self.audio_service,
                )
                bundles[bundle.bundle_id] = bundle

        logger.debug(f"Imported {len(bundles)} audio files as bundles")
        return bundles

    def gather_bundles(self, input_dir: Path, store_dir: Path) -> BundleCache:
        """Gather all bundles to process from input and store directories.

        Combines newly discovered audio files from the input directory with
        existing bundles from the managed store directory. This provides a
        complete set of bundles for the current transcription run.

        Args:
            input_dir: Directory containing new audio files to process.
            store_dir: Directory containing existing bundle metadata.

        Returns:
            Bundles indexed by persistent ID, ready for job generation.

        """
        logger.info(f"Gathering audio files from input directory: {input_dir}")
        bundles = self.gather_pending_audio_files(input_dir)

        logger.info(f"Gathering bundles from managed store directory: {store_dir}")
        bundles.update(
            TranscribeBundle.gather_existing_bundles(
                store_dir=store_dir,
                dry_run=self.dry_run,
                cleanup_bundle=True,
                config=self.config,
                fs_service=self.fs_service,
                audio_service=self.audio_service,
            ),
        )
        return bundles

    def gather_jobs(self, bundle_cache: BundleCache) -> list[BundleJobs]:
        """Generate the job queue for processing all bundles.

        Creates a list of BundleJobs, one per bundle, containing all the
        tasks needed to complete transcription for each bundle. When
        only_one_bundle mode is enabled, returns only the first bundle's jobs.

        Args:
            bundle_cache: Bundles indexed by persistent ID to generate jobs for.

        Returns:
            List of BundleJobs ready to be processed.

        """
        logger.debug(f"Gathering jobs from {len(bundle_cache)} bundles")

        # one BundleJobs per bundle
        jobs: list[BundleJobs] = [
            bundle_jobs
            for bundle in bundle_cache.values()
            if (
                bundle_jobs := self.gather_bundle_jobs(
                    bundle,
                    self.config.general.store_dir,
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

        # Discovery treats a missing store as empty, so dry runs do not need to
        # create it before gathering bundles.
        if not self.dry_run:
            self.fs_service.ensure_directory_exists(store_dir)

        # Loading existing bundles
        # Keep a cache of loaded bundles for the time of this run.
        bundle_cache = self.gather_bundles(input_dir, store_dir)

        FilesystemActionRequestAdapter(self.config, self.fs_service).process_all(
            bundle_cache,
            dry_run=self.dry_run,
        )

        self.log_section_header("Gathering Jobs")
        all_bundle_jobs = self.gather_jobs(bundle_cache)
        errored_bundles = list[BundleJobs]()
        if not all_bundle_jobs:
            logger.info("No jobs found for processing")
        else:
            total_jobs = sum(len(bundle_jobs) for bundle_jobs in all_bundle_jobs)
            logger.info(f"Found {total_jobs} pending jobs for {len(all_bundle_jobs)} bundles")
            self.log_section_header("Processing Jobs")
            errored_bundles = self.process_jobs(all_bundle_jobs, bundle_cache)

        if not self.dry_run:
            remove_empty_subdirs(input_dir)

        self.log_section_header("Summary")
        logger.info("Transcription process finished.")
        if len(errored_bundles) > 0:
            logger.info(f"{len(errored_bundles)} bundle(s) were not fully processed.")
        else:
            logger.info("All bundles were processed successfully.")

        return errored_bundles
