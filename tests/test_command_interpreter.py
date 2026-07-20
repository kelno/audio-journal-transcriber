"""Tests for command_interpreter raising UnexpectedChatClientAnswerException."""

import pytest

from tests.fake_clients import FakeAudioClient, FakeChatClient
from transcriber.ai_manager import RealAIManager
from transcriber.config import TranscribeConfig
from transcriber.exception import UnexpectedChatClientAnswerException


def _ai_manager_with_response(response: str, fake_config: TranscribeConfig) -> RealAIManager:
    """Build an AIManager whose chat client returns the given response."""
    return RealAIManager(
        audio_client=FakeAudioClient(),
        chat_client=FakeChatClient(response=response),
        config=fake_config,
    )


class TestInterpretCommand:
    """Tests for interpret_command raising UnexpectedChatClientAnswerException."""

    def test_raises_on_unexpected_response(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """A response that is neither a known command nor UNKNOWN is rejected."""
        # "GIBBERISH" matches no CommandType and is not the literal "UNKNOWN".
        ai_manager = _ai_manager_with_response("GIBBERISH", fake_config)

        with pytest.raises(UnexpectedChatClientAnswerException):
            ai_manager.interpret_command("do the thing")
