"""Tests for TranscribeBundleJob implementations (creation, summary, etc.)."""

import pytest

from tests.bundle_fixtures import TranscribeBundleFactory
from tests.fake_ai_manager import FakeAIManager
from tests.fake_audio_service import FakeAudioService
from tests.fake_file_system import FakeFileSystemService
from transcriber.config import TranscribeConfig
from transcriber.constants import (
    CUSTOM_CONTEXT_FILENAME,
    DEFAULT_CUSTOM_CONTEXT_CONTENT,
)
from transcriber.exception import EmptyTranscriptException, TooShortException
from transcriber.transcribe_bundle import TranscribeBundle
from transcriber.transcribe_bundle_job import (
    CreateBundleJob,
    SummaryJob,
    TranscriptionJob,
)


@pytest.fixture
def fake_ai_manager() -> FakeAIManager:
    """Create a fake AI manager (no real API calls)."""
    return FakeAIManager()


class TestCreateBundleJob:
    """Tests for CreateBundleJob writing the custom_context.md file."""

    def test_creates_custom_context_file_when_missing(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        fake_ai_manager: FakeAIManager,
    ) -> None:
        """A new bundle gets a seeded custom_context.md with the default header."""
        input_audio = fake_config.general.input_dir / "voice_memo.mp3"
        fake_fs.write_file(input_audio, "Audio")
        fake_audio_service.set_audio_duration(input_audio, 20.0)

        bundle = TranscribeBundle.from_audio_file(
            source_audio=input_audio,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        job = CreateBundleJob(bundle, dry_run=False)
        job.run(fake_ai_manager, fake_config, {bundle.bundle_id: bundle})

        bundle_dir = bundle.get_bundle_dir()
        assert fake_fs.file_exists(bundle_dir / CUSTOM_CONTEXT_FILENAME)
        content = fake_fs.read_file(bundle_dir / CUSTOM_CONTEXT_FILENAME)
        assert content == DEFAULT_CUSTOM_CONTEXT_CONTENT

    def test_uses_id_suffix_instead_of_overwriting_existing_bundle_directory(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        fake_ai_manager: FakeAIManager,
    ) -> None:
        """A colliding human-readable directory remains entirely untouched."""
        input_audio = fake_config.general.input_dir / "voice_memo.mp3"
        fake_fs.write_file(input_audio, "Audio")
        fake_audio_service.set_audio_duration(input_audio, 20.0)

        bundle = TranscribeBundle.from_audio_file(
            source_audio=input_audio,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        preferred_bundle_dir = bundle.get_bundle_dir()
        user_content = "User is very drunk in this recording.\n"
        fake_fs.write_file(preferred_bundle_dir / CUSTOM_CONTEXT_FILENAME, user_content)

        job = CreateBundleJob(bundle, dry_run=False)
        job.run(fake_ai_manager, fake_config, {bundle.bundle_id: bundle})

        assert bundle.get_bundle_dir() != preferred_bundle_dir
        assert bundle.bundle_name.endswith(f"~{bundle.bundle_id[:8]}")
        assert fake_fs.read_file(preferred_bundle_dir / CUSTOM_CONTEXT_FILENAME) == user_content
        assert fake_fs.read_file(bundle.get_bundle_dir() / CUSTOM_CONTEXT_FILENAME) == DEFAULT_CUSTOM_CONTEXT_CONTENT

    def test_dry_run_does_not_create_custom_context_file(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        fake_ai_manager: FakeAIManager,
    ) -> None:
        """In dry-run mode no custom_context.md is written."""
        input_audio = fake_config.general.input_dir / "voice_memo.mp3"
        fake_fs.write_file(input_audio, "Audio")
        fake_audio_service.set_audio_duration(input_audio, 20.0)

        bundle = TranscribeBundle.from_audio_file(
            source_audio=input_audio,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        job = CreateBundleJob(bundle, dry_run=True)
        job.run(fake_ai_manager, fake_config, {bundle.bundle_id: bundle})

        assert not fake_fs.file_exists(bundle.get_bundle_dir() / CUSTOM_CONTEXT_FILENAME)

    def test_raises_too_short_exception(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        fake_ai_manager: FakeAIManager,
    ) -> None:
        """An audio file shorter than the configured minimum is refused."""
        # Enable the minimum-length check via config.
        fake_config.general.min_length_seconds = 10.0
        input_audio = fake_config.general.input_dir / "voice_memo.mp3"
        fake_fs.write_file(input_audio, "Audio")
        # Duration below the threshold.
        fake_audio_service.set_audio_duration(input_audio, 5.0)

        bundle = TranscribeBundle.from_audio_file(
            source_audio=input_audio,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        job = CreateBundleJob(bundle, dry_run=False)

        with pytest.raises(TooShortException) as exc_info:
            job.run(fake_ai_manager, fake_config, {bundle.bundle_id: bundle})

        # The original source path is preserved on the exception for diagnostics.
        assert exc_info.value.source_audio == input_audio
        # The too-short file must not have been moved into the bundle.
        assert not fake_fs.file_exists(bundle.get_bundle_dir() / "voice_memo.mp3")


class TestTranscriptionJob:
    """Tests for TranscriptionJob raising EmptyTranscriptException."""

    def test_raises_on_empty_transcript(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
    ) -> None:
        """A transcription that yields empty content aborts the job."""
        input_audio = fake_config.general.input_dir / "voice_memo.mp3"
        fake_fs.write_file(input_audio, "Audio")
        fake_audio_service.set_audio_duration(input_audio, 20.0)

        bundle = TranscribeBundle.from_audio_file(
            source_audio=input_audio,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        # Fake audio client returns an empty/whitespace-only transcript.
        ai_manager = FakeAIManager(transcript="   ")
        job = TranscriptionJob(bundle, dry_run=False)

        with pytest.raises(EmptyTranscriptException):
            job.run(ai_manager, fake_config, {bundle.bundle_id: bundle})

        # No transcript file should have been written for an empty result.
        assert bundle.transcript is None

    def test_persists_context_hash_after_summary(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        fake_ai_manager: FakeAIManager,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """SummaryJob writes summary_context_hash into metadata."""
        bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_memo",
            audio_filename="memo.mp3",
            transcript_text="This is the transcript.",
        )
        # Precondition: hash was seeded at bundle creation (init_metadata).
        assert bundle.metadata.summary_context_hash is not None

        job = SummaryJob(bundle, dry_run=False)
        job.run(fake_ai_manager, fake_config, {bundle.bundle_id: bundle})

        assert bundle.metadata.summary_context_hash == bundle.compute_context_hash()
        # And it was persisted to disk.
        reloaded = TranscribeBundle.from_existing_directory(
            existing_dir=bundle.get_bundle_dir(),
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        assert reloaded.metadata.summary_context_hash == bundle.compute_context_hash()

    def test_hash_reflects_user_provided_context(
        self,
        fake_config: TranscribeConfig,
        fake_ai_manager: FakeAIManager,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """The persisted hash accounts for real custom_context content."""
        bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_memo",
            audio_filename="memo.mp3",
            transcript_text="This is the transcript.",
            custom_context="This recording was recorded with a potato in my mouth.\n",
        )
        job = SummaryJob(bundle, dry_run=False)
        job.run(fake_ai_manager, fake_config, {bundle.bundle_id: bundle})

        # The hash must match the effective context (which now includes the
        # user-provided line), not the empty/default context.
        assert bundle.metadata.summary_context_hash == bundle.compute_context_hash()
        assert bundle.metadata.summary_context_hash is not None
        assert bundle.get_effective_context() == "This recording was recorded with a potato in my mouth."
