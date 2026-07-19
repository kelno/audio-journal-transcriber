"""Tests for AudioTranscriber summary scheduling (context-change regeneration)."""

import pytest

from tests.bundle_fixtures import TranscribeBundleFactory
from tests.fake_audio_service import FakeAudioService
from tests.fake_clients import FakeAudioClient, FakeChatClient
from tests.fake_file_system import FakeFileSystemService
from transcriber.ai_manager import AIManager
from transcriber.audio_transcriber import AudioTranscriber
from transcriber.config import TranscribeConfig
from transcriber.constants import CUSTOM_CONTEXT_FILENAME
from transcriber.files.text_file import CustomContextFile
from transcriber.transcribe_bundle_job import BundleNameJob, SummaryJob, TranscribeBundleJob


@pytest.fixture
def audio_transcriber(
    fake_config: TranscribeConfig,
    fake_fs: FakeFileSystemService,
    fake_audio_service: FakeAudioService,
) -> AudioTranscriber:
    """AudioTranscriber with fake clients (no real API/FS access needed)."""
    ai_manager = AIManager(
        audio_client=FakeAudioClient(),
        chat_client=FakeChatClient(),
        config=fake_config,
    )
    return AudioTranscriber(
        dry_run=True,
        ai_manager=ai_manager,
        config=fake_config,
        fs_service=fake_fs,
        audio_service=fake_audio_service,
    )


class TestSummaryScheduling:
    """Tests that SummaryJob is scheduled based on transcript/context state."""

    def _has_summary_job(self, jobs: list[TranscribeBundleJob]) -> bool:
        return any(isinstance(job, SummaryJob) for job in jobs)

    def _has_name_job(self, jobs: list[TranscribeBundleJob]) -> bool:
        return any(isinstance(job, BundleNameJob) for job in jobs)

    def test_no_summary_with_transcript_schedules(
        self,
        audio_transcriber: AudioTranscriber,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """A bundle with a transcript but no summary schedules a SummaryJob."""
        bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_memo",
            audio_filename="memo.mp3",
            transcript_text="This is the transcript.",
        )
        jobs = audio_transcriber.gather_bundle_jobs(bundle, bundle.get_bundle_dir().parent, True, audio_transcriber.config)
        assert self._has_summary_job(jobs) is True

    def test_existing_summary_with_unchanged_context_skips(
        self,
        audio_transcriber: AudioTranscriber,
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
        jobs = audio_transcriber.gather_bundle_jobs(bundle, bundle.get_bundle_dir().parent, True, audio_transcriber.config)
        assert self._has_summary_job(jobs) is False

    def test_existing_summary_with_changed_context_schedules(
        self,
        audio_transcriber: AudioTranscriber,
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

        jobs = audio_transcriber.gather_bundle_jobs(bundle, bundle.get_bundle_dir().parent, True, audio_transcriber.config)
        assert self._has_summary_job(jobs) is True
        # The summary changed, so the name job must also chain to recompute it.
        assert self._has_name_job(jobs) is True

    def test_existing_summary_with_removed_context_schedules(
        self,
        audio_transcriber: AudioTranscriber,
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
        jobs = audio_transcriber.gather_bundle_jobs(bundle, bundle.get_bundle_dir().parent, True, audio_transcriber.config)
        assert self._has_summary_job(jobs) is True
