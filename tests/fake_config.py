from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.fake_file_system import FakeFileSystemService
from transcriber.config import AudioConfig, GeneralConfig, TextConfig, TranscribeConfig
from transcriber.files.metadata import MetadataFile
from transcriber.transcribe_bundle import TranscribeBundle


@pytest.fixture
def fake_config() -> TranscribeConfig:
    """Create a minimal config for testing."""
    return TranscribeConfig(
        general=GeneralConfig(
            input_dir=Path("/fake/input"),
            store_dir=Path("/fake/store"),
            delete_source_audio_after_days=0,
            min_length_seconds=0.0,
            remove_short_files=False,
            timezone=ZoneInfo("UTC"),
            safe_delete=True,
            merge_max_hours=12.0,
        ),
        text=TextConfig(
            summary_enabled=True,
            api_base_url="https://api.example.com",
            model="gpt-4",
            api_key="test-key",
            extra_context=None,
        ),
        audio=AudioConfig(
            api_base_url="https://api.example.com",
            model="whisper-1",
            api_key="test-key",
            stream=False,
        ),
    )


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
    fake_config: TranscribeConfig,
    fake_fs: FakeFileSystemService,
    generic_bundle_dir: Path,
) -> TranscribeBundle:
    """Create a generic bundle, containing only the audio & metadata.

    Will create directory, metadata and audio file in dir.
    """
    audio_filename = "meeting.mp3"
    bundle = TranscribeBundle(
        bundle_name=generic_bundle_dir.name,
        metadata=MetadataFile(original_audio_filenames=[audio_filename]),
        source_audios=[generic_bundle_dir / audio_filename],
        transcript=None,
        summary=None,
        commands=None,
        fs_service=fake_fs,
        config=fake_config,
    )

    bundle.init_metadata([audio_filename], [3600.0])  # create valid metadata + write file
    fake_fs.write_file(generic_bundle_dir / audio_filename, "Audio")
    return bundle
