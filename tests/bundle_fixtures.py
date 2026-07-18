"""Shared pytest fixtures for transcribe bundle tests."""

from pathlib import Path
from typing import Protocol

import pytest

from tests.fake_audio_service import FakeAudioService
from tests.fake_file_system import FakeFileSystemService
from transcriber.config import TranscribeConfig
from transcriber.files.commands_file import CommandsFile
from transcriber.files.metadata import AudioFileMeta, MetadataFile
from transcriber.files.text_file import SummaryFile, TranscriptFile
from transcriber.transcribe_bundle import TranscribeBundle


class TranscribeBundleFactory(Protocol):
    def __call__(
        self,
        bundle_name: str,
        audio_filename: str,
        transcript_text: str | None = None,
        summary_text: str | None = None,
        commands: list[str] | None = None,
        commands_executed: bool = False,
        transcript_model_used: list[str] | str | None = None,
        keep_forever: bool = False,
        config: TranscribeConfig | None = None,
    ) -> TranscribeBundle:
        """Factory callable returning a `TranscribeBundle` for tests."""
        ...


@pytest.fixture
def transcribe_bundle_factory(
    fake_config: TranscribeConfig,
    fake_fs: FakeFileSystemService,
    fake_audio_service: FakeAudioService,
) -> TranscribeBundleFactory:
    """Pytest fixture that returns a factory for creating test bundles."""

    def _create_bundle(
        bundle_name: str,
        audio_filename: str,
        transcript_text: str | None = None,
        summary_text: str | None = None,
        commands: list[str] | None = None,
        commands_executed: bool = False,
        transcript_model_used: list[str] | str | None = None,
        keep_forever: bool = False,
        config: TranscribeConfig | None = None,
    ) -> TranscribeBundle:
        config = config or fake_config
        bundle_dir = config.general.store_dir / bundle_name
        fake_fs.create_directory(bundle_dir)

        fake_audio_service.set_audio_duration(bundle_dir / audio_filename, 20.0)

        bundle = TranscribeBundle(
            bundle_name=bundle_dir.name,
            metadata=MetadataFile(audio_files=[AudioFileMeta(filename=audio_filename)]),
            source_audios=[bundle_dir / audio_filename],
            transcript=TranscriptFile(transcript_text) if transcript_text else None,
            summary=SummaryFile(summary_text) if summary_text else None,
            commands=CommandsFile.from_command_list(commands) if commands else None,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
            config=config,
        )
        bundle.init_metadata([audio_filename])

        if transcript_model_used is not None:
            models = [transcript_model_used] if isinstance(transcript_model_used, str) else transcript_model_used
            if bundle.metadata.audio_files:
                bundle.metadata.audio_files[0].transcript_model_used = models
        if keep_forever:
            bundle.metadata.keep_forever = keep_forever
        bundle.metadata.write(bundle_dir, fake_fs)

        if commands and bundle.commands:
            if commands_executed:
                for command in bundle.commands.commands:
                    command.executed = True
            bundle.commands.write(bundle_dir, fake_fs)

        fake_fs.write_file(bundle_dir / audio_filename, "Audio")

        if transcript_text and bundle.transcript:
            bundle.transcript.write(bundle_dir, fake_fs)
        if summary_text and bundle.summary:
            bundle.summary.write(bundle_dir, fake_fs)

        return bundle

    return _create_bundle


@pytest.fixture
def generic_bundle_dir(
    fake_fs: FakeFileSystemService,
    fake_config: TranscribeConfig,
) -> Path:
    """Get a generic bundle directory, creating it in filesystem."""
    bundle_name = "2025-01-15_meeting"
    bundle_dir = fake_config.general.store_dir / bundle_name
    fake_fs.create_directory(bundle_dir)
    return bundle_dir


@pytest.fixture
def generic_bundle(
    generic_bundle_dir: Path,
    transcribe_bundle_factory: TranscribeBundleFactory,
) -> TranscribeBundle:
    """Create a generic bundle, containing only the audio & metadata.

    Uses the shared bundle factory to reduce duplicate setup logic.
    """
    return transcribe_bundle_factory(
        bundle_name=generic_bundle_dir.name,
        audio_filename="meeting.mp3",
    )
