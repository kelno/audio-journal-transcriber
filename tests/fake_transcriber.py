import pytest

from tests.fake_ai_manager import FakeAIManager
from tests.fake_audio_service import FakeAudioService
from tests.fake_file_system import FakeFileSystemService
from transcriber.audio_transcriber import AudioTranscriber
from transcriber.config import TranscribeConfig


@pytest.fixture
def fake_transcriber(
    fake_config: TranscribeConfig,
    fake_fs: FakeFileSystemService,
    fake_audio_service: FakeAudioService,
    fake_ai_manager: FakeAIManager,
) -> AudioTranscriber:
    """Non-dry-run AudioTranscriber so commands and summaries actually execute."""
    return AudioTranscriber(
        dry_run=False,
        ai_manager=fake_ai_manager,
        config=fake_config,
        fs_service=fake_fs,
        audio_service=fake_audio_service,
    )
