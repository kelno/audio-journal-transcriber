"""Shared pytest fixtures for transcribe bundle tests."""

from datetime import datetime
from pathlib import Path
from typing import Protocol

import pytest

from tests.fake_audio_service import FakeAudioService
from tests.fake_file_system import FakeFileSystemService
from transcriber.bundle_id import BundleId, new_bundle_id
from transcriber.config import TranscribeConfig
from transcriber.files.commands_file import CommandsFile
from transcriber.files.metadata import AudioFileMeta, MetadataFile
from transcriber.files.text_file import (
    CustomContextFile,
    SummaryFile,
    TranscriptFile,
)
from transcriber.transcribe_bundle import TranscribeBundle


class TranscribeBundleFactory(Protocol):
    def __call__(
        self,
        bundle_name: str,
        audio_filename: str,
        bundle_date: datetime | None = None,  # if None, use now()
        transcript_text: str | None = None,
        summary_text: str | None = None,
        commands: list[str] | None = None,
        commands_executed: bool = False,
        transcript_model_used: list[str] | str | None = None,
        keep_forever: bool = False,
        config: TranscribeConfig | None = None,
        custom_context: str | None = None,
        bundle_id: BundleId | None = None,
    ) -> TranscribeBundle:
        """Create a persisted test bundle with optional explicit identity.

        Args:
            bundle_name: Human-readable bundle directory name.
            audio_filename: Source audio filename stored in the bundle.
            bundle_date: Canonical bundle date, defaulting to the current time.
            transcript_text: Optional transcript content.
            summary_text: Optional summary content.
            commands: Optional raw command strings.
            commands_executed: Whether supplied commands start as executed.
            transcript_model_used: Optional model name or ordered model names.
            keep_forever: Whether source audio is exempt from deletion.
            config: Optional configuration overriding the fixture default.
            custom_context: Optional recording-specific summary context.
            bundle_id: Optional persistent ID, primarily for duplicate-ID tests.

        Returns:
            The created test bundle.

        """
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
        bundle_date: datetime | None = None,
        transcript_text: str | None = None,
        summary_text: str | None = None,
        commands: list[str] | None = None,
        commands_executed: bool = False,
        transcript_model_used: list[str] | str | None = None,
        keep_forever: bool = False,
        config: TranscribeConfig | None = None,
        custom_context: str | None = None,
        bundle_id: BundleId | None = None,
    ) -> TranscribeBundle:
        """Implement the test bundle factory protocol.

        Args:
            bundle_name: Human-readable bundle directory name.
            audio_filename: Source audio filename stored in the bundle.
            bundle_date: Canonical bundle date, defaulting to the current time.
            transcript_text: Optional transcript content.
            summary_text: Optional summary content.
            commands: Optional raw command strings.
            commands_executed: Whether supplied commands start as executed.
            transcript_model_used: Optional model name or ordered model names.
            keep_forever: Whether source audio is exempt from deletion.
            config: Optional configuration overriding the fixture default.
            custom_context: Optional recording-specific summary context.
            bundle_id: Optional persistent ID, primarily for duplicate-ID tests.

        Returns:
            The created and persisted test bundle.

        """
        config = config or fake_config
        bundle_dir = config.general.store_dir / bundle_name
        fake_fs.create_directory(bundle_dir)

        fake_audio_service.set_audio_duration(bundle_dir / audio_filename, 20.0)

        bundle = TranscribeBundle(
            bundle_name=bundle_dir.name,
            metadata=MetadataFile(
                bundle_id=bundle_id or new_bundle_id(),
                audio_files=[AudioFileMeta(filename=audio_filename)],
                bundle_date=bundle_date or datetime.now(config.general.timezone),
            ),
            source_audios=[bundle_dir / audio_filename],
            transcript=TranscriptFile(transcript_text) if transcript_text else None,
            summary=SummaryFile(summary_text) if summary_text else None,
            commands=CommandsFile.from_command_list(commands) if commands else None,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
            config=config,
            custom_context=CustomContextFile(custom_context) if custom_context else None,
        )
        bundle.init_metadata([audio_filename])
        bundle.init_custom_context()

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

        if bundle.transcript:
            bundle.transcript.write(bundle_dir, fake_fs)

        if bundle.summary:
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
    fake_config: TranscribeConfig,
    transcribe_bundle_factory: TranscribeBundleFactory,
) -> TranscribeBundle:
    """Create a generic bundle, containing only the audio & metadata.

    Uses the shared bundle factory to reduce duplicate setup logic.
    """
    return transcribe_bundle_factory(
        bundle_name=generic_bundle_dir.name,
        audio_filename="meeting.mp3",
        bundle_date=datetime(
            year=2025,
            month=1,
            day=15,
            hour=14,
            minute=30,
            tzinfo=fake_config.general.timezone,
        ),
    )
