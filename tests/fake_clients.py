from pathlib import Path
from typing import final, override

from transcriber.clients.clients import AudioTranscriptionClient, ChatCompletionClient


@final
class FakeAudioClient(AudioTranscriptionClient):
    """Test fake that returns hardcoded transcriptions."""

    def __init__(self, response: str = "This is a test transcription."):
        """Initialize with a response to return.

        Args:
            response: The text to return when transcribe() is called.

        """
        self.response = response
        self.transcribed_files: list[Path] = []  # Track which files were transcribed

    @override
    def transcribe(self, audio_path: Path) -> str:
        """Return the fake response, tracking which files were transcribed."""
        self.transcribed_files.append(audio_path)
        return self.response


@final
class FakeChatClient(ChatCompletionClient):
    """Test fake that returns hardcoded completions."""

    def __init__(self, response: str = "Test response"):
        """Initialize with a response to return.

        Args:
            response: The text to return when create_completion() is called.

        """
        self.response = response
        self.completion_calls: list[list[dict[str, str]]] = []  # Track all calls made

    @override
    def create_completion(self, messages: list[dict[str, str]]) -> str:
        """Return the fake response, tracking which prompts were sent."""
        self.completion_calls.append(messages)
        return self.response


@final
class FakeChatClientWithErrors(ChatCompletionClient):
    """Test fake that simulates API errors."""

    # class const strings
    ERROR_MESSAGE = "Simulated API error for testing"

    @override
    def create_completion(self, messages: list[dict[str, str]]) -> str:
        """Raise an error to simulate API failure."""
        msg = f"{self.ERROR_MESSAGE}: {messages}"
        raise ValueError(msg)
