"""Tests for TranscribeBundleJob implementations (creation, summary, etc.)."""

import pytest

from tests.fake_audio_service import FakeAudioService
from tests.fake_clients import FakeAudioClient, FakeChatClient
from tests.fake_file_system import FakeFileSystemService
from transcriber.ai_manager import AIManager
from transcriber.config import TranscribeConfig
from transcriber.constants import (
    CUSTOM_CONTEXT_FILENAME,
    DEFAULT_CUSTOM_CONTEXT_CONTENT,
)
from transcriber.transcribe_bundle import TranscribeBundle
from transcriber.transcribe_bundle_job import CreateBundleJob


@pytest.fixture
def fake_ai_manager(fake_config: TranscribeConfig) -> AIManager:
    """Create an AIManager backed by fake clients (no real API calls)."""
    return AIManager(
        audio_client=FakeAudioClient(),
        chat_client=FakeChatClient(),
        config=fake_config,
    )


class TestCreateBundleJob:
    """Tests for CreateBundleJob writing the custom_context.md file."""

    def test_creates_custom_context_file_when_missing(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        fake_ai_manager: AIManager,
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
        job.run(fake_ai_manager, fake_config)

        bundle_dir = bundle.get_bundle_dir()
        assert fake_fs.file_exists(bundle_dir / CUSTOM_CONTEXT_FILENAME)
        content = fake_fs.read_file(bundle_dir / CUSTOM_CONTEXT_FILENAME)
        assert content == DEFAULT_CUSTOM_CONTEXT_CONTENT

    def test_does_not_overwrite_existing_custom_context_file(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        fake_ai_manager: AIManager,
    ) -> None:
        """An existing (user-provided) custom_context.md is left untouched."""
        input_audio = fake_config.general.input_dir / "voice_memo.mp3"
        fake_fs.write_file(input_audio, "Audio")
        fake_audio_service.set_audio_duration(input_audio, 20.0)

        bundle = TranscribeBundle.from_audio_file(
            source_audio=input_audio,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        bundle_dir = bundle.get_bundle_dir()
        user_content = "User is very drunk in this recording.\n"
        # Simulate a user having already created the file before the job runs.
        fake_fs.write_file(bundle_dir / CUSTOM_CONTEXT_FILENAME, user_content)

        job = CreateBundleJob(bundle, dry_run=False)
        job.run(fake_ai_manager, fake_config)

        assert fake_fs.read_file(bundle_dir / CUSTOM_CONTEXT_FILENAME) == user_content

    def test_dry_run_does_not_create_custom_context_file(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        fake_ai_manager: AIManager,
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
        job.run(fake_ai_manager, fake_config)

        assert not fake_fs.file_exists(bundle.get_bundle_dir() / CUSTOM_CONTEXT_FILENAME)
