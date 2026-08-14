"""Tests for OpenAI-compatible HTTP clients."""

from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from transcriber.clients.openai_clients import OpenAIAudioClient
from transcriber.config import AudioConfig
from transcriber.exception import TranscriptionRequestException

if TYPE_CHECKING:
    import requests


def test_audio_client_raises_specific_exception_for_http_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unsuccessful response preserves details needed by future retry policy."""
    audio_path = tmp_path / "invalid.mp3"
    audio_path.write_bytes(b"invalid audio")
    raw_response = Mock(status_code=400, text="Invalid audio data")
    response = cast("requests.Response", raw_response)
    post = Mock(return_value=response)
    monkeypatch.setattr("transcriber.clients.openai_clients.requests.post", post)
    client = OpenAIAudioClient(
        AudioConfig(
            api_base_url="http://audio.example/v1",
            model="test-model",
            api_key="test-key",
            stream=True,
        ),
    )

    with pytest.raises(TranscriptionRequestException) as caught:
        client.transcribe(audio_path)

    assert caught.value.status_code == 400
    assert caught.value.response_body == "Invalid audio data"
    assert str(caught.value) == "Transcription failed with status code 400: Invalid audio data"
