from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from transcriber.config import AudioConfig, GeneralConfig, TextConfig, TranscribeConfig


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
