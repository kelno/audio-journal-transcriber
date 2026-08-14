from abc import ABC, abstractmethod
from pathlib import Path


class AudioTranscriptionClient(ABC):
    """Abstract interface for audio transcription services.

    This allows us to swap between real APIs (OpenAI, etc.) and test fakes.
    """

    @abstractmethod
    def transcribe(self, audio_path: Path) -> str:
        """Transcribe an audio file.

        Args:
            audio_path: Path to the audio file to transcribe.

        Returns:
            str: The transcribed text.

        Raises:
            TranscriptionRequestException: If the service returns an unsuccessful HTTP response.
            FileNotFoundError: If the audio file doesn't exist.

        """


class ChatCompletionClient(ABC):
    """Abstract interface for chat completion services.

    This allows us to swap between real APIs (OpenAI, etc.) and test fakes.
    """

    @abstractmethod
    def create_completion(self, messages: list[dict[str, str]]) -> str:
        """Generate a chat completion.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
                Example: [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Hello!"}
                ]

        Returns:
            str: The assistant's response.

        Raises:
            ValueError: If completion fails or returns invalid response.

        """
