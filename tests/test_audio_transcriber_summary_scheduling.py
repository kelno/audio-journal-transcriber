"""Tests for AudioTranscriber summary scheduling (context-change regeneration)."""

from datetime import datetime, timedelta

from tests.bundle_fixtures import TranscribeBundleFactory
from tests.fake_ai_manager import FakeAIManager
from tests.fake_file_system import FakeFileSystemService
from transcriber.audio_transcriber import AudioTranscriber
from transcriber.bundle_title import BundleTitleState
from transcriber.commands.command_interpretation import ArgumentlessCommandInterpretation
from transcriber.commands.command_type import CommandType
from transcriber.config import TranscribeConfig
from transcriber.constants import CUSTOM_CONTEXT_FILENAME, SUMMARY_FILENAME
from transcriber.files.text_file import CustomContextFile
from transcriber.transcribe_bundle import TranscribeBundle
from transcriber.transcribe_bundle_job import BundleNameJob, SummaryJob, TranscribeBundleJob


class TestSummaryScheduling:
    """Tests that SummaryJob is scheduled based on transcript/context state."""

    def _has_summary_job(self, jobs: list[TranscribeBundleJob]) -> bool:
        return any(isinstance(job, SummaryJob) for job in jobs)

    def _has_name_job(self, jobs: list[TranscribeBundleJob]) -> bool:
        return any(isinstance(job, BundleNameJob) for job in jobs)

    def test_no_summary_with_transcript_schedules(
        self,
        fake_transcriber: AudioTranscriber,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """A bundle with a transcript but no summary schedules a SummaryJob."""
        bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_memo",
            audio_filename="memo.mp3",
            transcript_text="This is the transcript.",
        )
        jobs = fake_transcriber.gather_bundle_jobs(bundle, bundle.get_bundle_dir().parent, True, fake_transcriber.config)
        assert self._has_summary_job(jobs) is True

    def test_existing_summary_with_unchanged_context_skips(
        self,
        fake_transcriber: AudioTranscriber,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """When context is unchanged, an existing summary is not regenerated."""
        bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_memo",
            audio_filename="memo.mp3",
            transcript_text="This is the transcript.",
            summary_text="Existing summary.",
        )
        # Hash not yet persisted: set it to the current effective context hash.
        bundle.metadata.summary_context_hash = bundle.compute_context_hash()
        jobs = fake_transcriber.gather_bundle_jobs(bundle, bundle.get_bundle_dir().parent, True, fake_transcriber.config)
        assert self._has_summary_job(jobs) is False

    def test_existing_summary_with_changed_context_schedules(
        self,
        fake_transcriber: AudioTranscriber,
        fake_fs: FakeFileSystemService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """When custom_context.md changed, an existing summary is regenerated."""
        bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_memo",
            audio_filename="memo.mp3",
            transcript_text="This is the transcript.",
            summary_text="Existing summary.",
        )
        bundle.metadata.summary_context_hash = bundle.compute_context_hash()
        fake_fs.write_file(
            bundle.get_bundle_dir() / CUSTOM_CONTEXT_FILENAME,
            "This is a message to my team lead.\n",
        )
        bundle.custom_context = CustomContextFile("This is a message to my team lead.\n")

        jobs = fake_transcriber.gather_bundle_jobs(bundle, bundle.get_bundle_dir().parent, True, fake_transcriber.config)
        assert self._has_summary_job(jobs) is True
        # The summary changed, so the name job must also chain to recompute it.
        assert self._has_name_job(jobs) is True

    def test_existing_summary_with_removed_context_schedules(
        self,
        fake_transcriber: AudioTranscriber,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Removing context after it was filled triggers regeneration."""
        bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_memo",
            audio_filename="memo.mp3",
            transcript_text="This is the transcript.",
            summary_text="Existing summary.",
        )
        # Simulate a previously-filled context hash that no longer matches.
        bundle.metadata.summary_context_hash = "deadbeefdeadbeef"
        jobs = fake_transcriber.gather_bundle_jobs(bundle, bundle.get_bundle_dir().parent, True, fake_transcriber.config)
        assert self._has_summary_job(jobs) is True

    def test_changed_context_preserves_manual_title_and_skips_name_job(
        self,
        fake_transcriber: AudioTranscriber,
        fake_fs: FakeFileSystemService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Summary regeneration does not schedule replacement of a manual title."""
        bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_manual",
            audio_filename="memo.mp3",
            transcript_text="This is the transcript.",
            summary_text="Existing summary.",
        )
        bundle.metadata.bundle_title_state = BundleTitleState.MANUAL
        bundle.metadata.summary_context_hash = bundle.compute_context_hash()
        fake_fs.write_file(
            bundle.get_bundle_dir() / CUSTOM_CONTEXT_FILENAME,
            "New context\n",
        )
        bundle.custom_context = CustomContextFile("New context\n")

        jobs = fake_transcriber.gather_bundle_jobs(bundle, bundle.get_bundle_dir().parent, True, fake_transcriber.config)

        assert self._has_summary_job(jobs) is True
        assert self._has_name_job(jobs) is False


class TestManualBundleTitleScheduling:
    """Manual titles remain authoritative even when jobs were queued earlier."""

    def test_summary_job_does_not_invalidate_manual_title(
        self,
        fake_ai_manager: FakeAIManager,
        fake_config: TranscribeConfig,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Regenerating summary content leaves manual title state unchanged."""
        bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_manual",
            audio_filename="memo.mp3",
            transcript_text="Transcript",
            summary_text="Old summary",
        )
        bundle.metadata.bundle_title_state = BundleTitleState.MANUAL

        SummaryJob(bundle, dry_run=False).run(
            fake_ai_manager,
            fake_config,
            {bundle.bundle_id: bundle},
        )

        assert bundle.metadata.bundle_title_state is BundleTitleState.MANUAL

    def test_previously_queued_name_job_skips_manual_title(
        self,
        fake_ai_manager: FakeAIManager,
        fake_config: TranscribeConfig,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """A name job rechecks state instead of overwriting a newer manual title."""
        bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_manual",
            audio_filename="memo.mp3",
            transcript_text="Transcript",
            summary_text="Summary",
        )
        bundle.metadata.bundle_title_state = BundleTitleState.MANUAL
        original_name = bundle.bundle_name

        BundleNameJob(bundle, dry_run=False).run(
            fake_ai_manager,
            fake_config,
            {bundle.bundle_id: bundle},
        )

        assert bundle.bundle_name == original_name
        assert fake_ai_manager.named_summaries == []


class TestMergeRequeue:
    """A merge clears the target's summary; it must be regenerated in the same run.

    These tests must run WITHOUT dry_run: RunCommandsJob and SummaryJob both no-op
    under dry_run, so the merge command would never fire and the summary would
    never regenerate. We build a non-dry-run transcriber explicitly here.
    """

    def test_merge_regenerates_target_summary_in_same_run(
        self,
        fake_transcriber: AudioTranscriber,
        fake_fs: FakeFileSystemService,
        fake_ai_manager: FakeAIManager,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """After a merge, the target's summary/name are regenerated within process_jobs.

        The target bundle is gathered first (so its stale jobs sit in the queue),
        then the source bundle whose merge command fires and re-enqueues the target.
        """
        tz = fake_transcriber.config.general.timezone
        target_date = datetime(year=2025, month=1, day=15, hour=20, tzinfo=tz)
        target = transcribe_bundle_factory(
            bundle_name="2025-01-15_target",
            audio_filename="target.mp3",
            bundle_date=target_date,
            transcript_text="target transcript",
            summary_text="stale summary that should be replaced",
        )
        source_raw_merge_cmd = "merge this recording with the previous one"
        source_transcript = f"source transcript, start command {source_raw_merge_cmd} end command"
        source = transcribe_bundle_factory(
            bundle_name="2025-01-15_source",
            audio_filename="source.mp3",
            bundle_date=target_date + timedelta(hours=2),
            transcript_text=source_transcript,
        )

        # prepare ai manager canned answers
        fake_transcriber.ai_manager = fake_ai_manager
        fake_transcriber.ai_manager.raw_commands[source_transcript] = [source_raw_merge_cmd]
        fake_transcriber.ai_manager.interpret_commands[source_raw_merge_cmd] = ArgumentlessCommandInterpretation(
            command_type=CommandType.MERGE,
        )
        post_merge_summary = "New summary from merged transcripts"
        fake_transcriber.ai_manager.summary = post_merge_summary

        bundle_cache = {
            source.bundle_id: source,
            target.bundle_id: target,
        }
        jobs = fake_transcriber.gather_jobs(bundle_cache)

        errored = fake_transcriber.process_jobs(jobs, bundle_cache)

        # The merge + regeneration completed in one pass: nothing left unprocessed.
        assert errored == []

        # Source bundle was deleted by the merge.
        assert not fake_fs.directory_exists(source.get_bundle_dir())

        # Target bundle's summary and name were regenerated (FakeChatClient returns "Test response").
        target_dir = target.get_bundle_dir()
        assert fake_fs.file_exists(target_dir / SUMMARY_FILENAME)
        assert fake_fs.read_file(target_dir / SUMMARY_FILENAME) == post_merge_summary

        # The regenerated summary implies a SummaryJob ran for the target.
        reloaded = TranscribeBundle.from_existing_directory(
            existing_dir=target_dir,
            config=fake_transcriber.config,
            fs_service=fake_fs,
            audio_service=fake_transcriber.audio_service,
        )
        assert reloaded.summary is not None
        assert reloaded.summary.text == post_merge_summary
        # Name regeneration is chained after summary, so the generated state is persisted.
        assert reloaded.metadata.bundle_title_state is BundleTitleState.GENERATED
