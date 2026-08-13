from dataclasses import dataclass
from pathlib import Path

from transcriber.actions.action_request import ActionEffects
from transcriber.actions.action_runtime import ActionRuntime
from transcriber.ai_manager import AIManager
from transcriber.audio_service import AudioService, RealAudioService
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
    ai_manager: AIManager
    config: TranscribeConfig
    fs_service: FileSystemService
    audio_service: AudioService
    action_runtime: ActionRuntime

    def __init__(
        self,
        dry_run: bool,
        ai_manager: AIManager,
        config: TranscribeConfig,
        fs_service: FileSystemService | None = None,
        audio_service: AudioService | None = None,
        action_runtime: ActionRuntime | None = None,
    ) -> None:
        """Initialize the audio transcriber."""
        self.dry_run = dry_run
        self.ai_manager = ai_manager
        self.config = config
        self.fs_service = fs_service or RealFileSystemService(config)
        self.audio_service = audio_service or RealAudioService()
        self.action_runtime = action_runtime or ActionRuntime.from_config(config, dry_run=dry_run)

    def __post_init__(self) -> None:
        """Initialize the audio transcriber."""
        if self.dry_run:
            logger.warning("!!! DRY RUN MODE !!!")
        logger.info(
            f"{type(self).__name__} initialized with\nGeneral configuration: {self.config.general}",
        )

    def _handle_process_jobs_exception(self, job: TranscribeBundleJob, e: Exception) -> bool:
        """Classify a job exception for logging and error reporting.

        Args:
            job: Job whose execution raised the exception.
            e: Exception raised by the job.

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

    def _apply_action_effects(
        self,
        effects: ActionEffects,
        queue: BundleJobQueue,
        bundle_cache: BundleCache,
        current_bundle_id: str,
        current_entry: BundleJobQueueEntry,
    ) -> bool:
        """Invalidate affected queue entries and derive fresh work for changed bundles."""
        # Removed bundles cannot produce any more work. Drop their queues before
        # considering changed bundles because an effect may defensively mention
        # the same ID in both collections.
        for removed_bundle_id in effects.removed_bundle_ids:
            queue.pop(removed_bundle_id, None)

        had_error = False
        removed_ids = set(effects.removed_bundle_ids)
        for changed_bundle_id in effects.changed_bundle_ids:
            if changed_bundle_id in removed_ids:
                continue

            # The bundle cache is the authoritative in-memory view after the
            # executor's mutation. A changed ID missing here means the effect and
            # the actual mutation disagree, so silently reusing old work is unsafe.
            bundle = bundle_cache.get(changed_bundle_id)
            if bundle is None:
                logger.error(f"Action reported changed bundle {changed_bundle_id}, but it is absent from the bundle cache.")
                had_error = True
                continue

            # Any jobs planned before the action observed stale bundle state.
            # Remove them and remember their requeue count before deriving a
            # replacement pipeline from the mutated bundle.
            existing = queue.pop(changed_bundle_id, None)
            previous_requeues = existing.requeue_count if existing is not None else 0

            # The current entry may already have been removed after its last job,
            # so its counter is not necessarily available through `queue` above.
            if changed_bundle_id == current_bundle_id:
                previous_requeues = max(previous_requeues, current_entry.requeue_count)

            # Effects should eventually converge to a bundle with no stale work.
            # This guard turns an accidental mutation/re-derivation cycle into a
            # visible error instead of allowing an infinite update loop.
            requeue_count = previous_requeues + 1
            if requeue_count > MAX_REQUEUE_PER_BUNDLE:
                logger.error(
                    f"Bundle {bundle.bundle_name} re-queued too many times; stopping to avoid a loop.",
                )
                had_error = True
                continue

            # Re-derive all job types rather than attempting to patch an existing
            # list. Bundle state remains the single source of truth for needed work.
            fresh_jobs = self.gather_bundle_jobs(
                bundle,
                self.config.general.store_dir,
                self.dry_run,
                config=self.config,
            )
            if fresh_jobs:
                queue[changed_bundle_id] = BundleJobQueueEntry(
                    jobs=fresh_jobs,
                    requeue_count=requeue_count,
                )
        return had_error

    @staticmethod
    def _select_oldest_ready_bundle(queue: BundleJobQueue) -> str | None:
        """Select the oldest bundle whose next job satisfies global readiness."""
        # Command work anywhere in the queue may still create a merge, delete, or
        # title action. Globally postponing summary and naming work avoids doing
        # expensive post-processing immediately before such an action invalidates it.
        has_command_work = any(
            isinstance(queued_job, (GatherCommandsJob, RunCommandsJob)) for entry in queue.values() for queued_job in entry.jobs
        )

        # Jobs within one bundle are already dependency ordered, so only its head
        # can be considered. A later job must never bypass an unready earlier one.
        ready_ids = [
            bundle_id
            for bundle_id, entry in queue.items()
            if entry.jobs and not (has_command_work and isinstance(entry.jobs[0], (SummaryJob, BundleNameJob)))
        ]
        if not ready_ids:
            return None

        # Derived jobs have no enqueue timestamp of their own. Recording date is
        # therefore the closest durable age key; or if those are also identical, bundle ID is used to makes ties deterministic.
        return min(
            ready_ids,
            key=lambda bundle_id: (queue[bundle_id].jobs[0].bundle.get_bundle_date(), bundle_id),
        )

    def process_jobs(self, all_jobs_bundles: list[BundleJobs], bundle_cache: BundleCache) -> list[BundleJobs]:
        """Process audio files from the pending directory.

        Args:
            all_jobs_bundles: List of bundle jobs to process.
            bundle_cache: Bundles indexed by persistent ID for job execution.

        Returns:
            list[BundleJobs]: Remaining unprocessed bundle jobs.

        """
        # Step 1: turn the gathered per-bundle job lists into an ID-keyed queue.
        # Human-readable bundle names can change during processing, whereas the
        # persistent ID remains safe for invalidation and replacement.
        queue: BundleJobQueue = {
            jobs_bundle[0].bundle.bundle_id: BundleJobQueueEntry(jobs=jobs_bundle) for jobs_bundle in all_jobs_bundles if jobs_bundle
        }

        errored_bundles = list[BundleJobs]()
        while queue:
            # Step 2: choose one ready bundle. Repeating selection after every job
            # lets newly produced effects change what is safe to execute next.
            bundle_id = self._select_oldest_ready_bundle(queue)
            if bundle_id is None:
                # A non-empty queue with no ready head violates the scheduler's
                # assumptions. Preserve all remaining work for retry/reporting
                # instead of spinning forever.
                logger.error("No queued bundle job is ready; stopping to avoid a scheduler loop.")
                errored_bundles.extend(entry.jobs for entry in queue.values() if entry.jobs)
                break

            entry = queue[bundle_id]
            # Step 3: execute only the head job. Its bundle-local successors may
            # depend on files or state that this job has not produced yet.
            job = entry.jobs[0]
            try:
                logger.debug(f"Processing job: {job}")
                effects = job.run(
                    ai_manager=self.ai_manager,
                    config=self.config,
                    bundle_cache=bundle_cache,
                )

                # Step 4: consume the completed job before applying effects. If
                # the action changed this bundle, re-derivation below will replace
                # the remaining stale jobs with a fresh pipeline.
                entry.jobs.pop(0)
                if not entry.jobs:
                    queue.pop(bundle_id)

                # Most jobs only advance their own pipeline. Action jobs can also
                # mutate or remove other bundles; their effects identify which
                # queued pipelines must be discarded and derived again.
                if effects is not None and (effects.changed_bundle_ids or effects.removed_bundle_ids):
                    errored = self._apply_action_effects(
                        effects,
                        queue,
                        bundle_cache,
                        bundle_id,
                        entry,
                    )
                    if errored and entry.jobs:
                        errored_bundles.append(entry.jobs)

                # Action effects may replace a seemingly completed pipeline with
                # fresh work, so report completion only after they are applied.
                if bundle_id not in queue:
                    logger.info(
                        f"Finished processing bundle [{job.bundle.bundle_name}]; "
                        f"bundles still with work in this run: {len(queue)}",
                    )
            except Exception as e:  # pylint: disable=broad-exception-caught
                # Step 5: one failing bundle must not prevent independent bundles
                # from progressing. Classify the failure, discard this bundle's
                # remaining queue, and retain reportable work for daemon retry.
                errored = self._handle_process_jobs_exception(job, e)
                queue.pop(bundle_id, None)
                if errored and entry.jobs:
                    errored_bundles.append(entry.jobs)

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
        tasks needed to complete transcription for each bundle.

        Args:
            bundle_cache: Bundles indexed by persistent ID to generate jobs for.

        Returns:
            List of BundleJobs ready to be processed.

        """
        logger.debug(f"Gathering jobs from {len(bundle_cache)} bundles")

        # Each bundle gets an internally ordered pipeline. Inter-bundle ordering
        # is deliberately deferred to `process_jobs`, where global readiness and
        # action effects can be considered after every completed job.
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

        # These phases define the dependency order within one bundle. Command
        # actions must be known before post-processing because they may invalidate
        # the bundle or change the content that should be summarized and named.
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
        # A deleted/empty bundle with no saved commands has no command source and
        # no outstanding command receipt to reconcile.
        if not bundle.source_audios and not bundle.commands:
            return []

        # The first visit extracts command text, then interprets/submits it in a
        # separate job. Keeping both jobs explicit makes their ordering visible to
        # the global summary-readiness rule.
        if not bundle.commands:
            return [
                GatherCommandsJob(bundle, dry_run),
                RunCommandsJob(bundle, dry_run, self.action_runtime),
            ]

        # Existing command state may contain an uninterpreted command, a request
        # awaiting execution, or a terminal request awaiting its command receipt.
        if bundle.commands.has_commands_needing_processing():
            return [RunCommandsJob(bundle, dry_run, self.action_runtime)]

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

        # Step 1: validate and prepare only the durable directories that this run
        # is allowed to mutate. Discovery treats a missing store as empty, so dry
        # runs do not create it merely by inspecting work.
        if not self.dry_run:
            self.fs_service.ensure_directory_exists(store_dir)

        # Step 2: build one current ID-keyed view from new input and stored bundles.
        # Executors and derived jobs share this cache for the duration of the update.
        bundle_cache = self.gather_bundles(input_dir, store_dir)

        # Step 3: execute externally submitted requests before deriving jobs. Their
        # mutations update the cache, so the jobs gathered next already describe
        # post-action state and do not require a separate invalidation pass here.
        self.action_runtime.process_external_requests(bundle_cache)

        # Step 4: derive reconstructible work from the resulting bundle state, then
        # let the coordinator execute one ready job at a time.
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

        # Step 5: remove input directories emptied by successful imports.
        if not self.dry_run:
            remove_empty_subdirs(input_dir)

        self.log_section_header("Summary")
        logger.info("Transcription process finished.")
        if len(errored_bundles) > 0:
            logger.info(f"{len(errored_bundles)} bundle(s) were not fully processed.")
        else:
            logger.info("All bundles were processed successfully.")

        return errored_bundles
