"""Tests for command_interpreter raising UnexpectedChatClientAnswerException."""

import pytest

from tests.fake_clients import FakeAudioClient, FakeChatClient
from transcriber.ai_manager import RealAIManager
from transcriber.commands.command_interpretation import (
    ArgumentlessCommandInterpretation,
    EmptyCommandArguments,
    SetTitleCommandArguments,
    SetTitleCommandInterpretation,
)
from transcriber.commands.command_type import CommandType
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
    """Tests for structured command interpretation."""

    def test_returns_structured_interpretation(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """A valid JSON response is parsed into a typed interpretation."""
        ai_manager = _ai_manager_with_response(
            '{"command_type": "merge", "arguments": {}}',
            fake_config,
        )

        interpretation = ai_manager.interpret_command("merge this recording")

        assert isinstance(interpretation, ArgumentlessCommandInterpretation)
        assert isinstance(interpretation.arguments, EmptyCommandArguments)
        assert interpretation.command_type is CommandType.MERGE

    def test_extracts_set_title_argument(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """SET_TITLE preserves the requested title in structured arguments."""
        ai_manager = _ai_manager_with_response(
            '{"command_type": "set_title", "arguments": {"title": "Planification trimestrielle"}}',
            fake_config,
        )

        interpretation = ai_manager.interpret_command("appelle cet enregistrement Planification trimestrielle")

        assert isinstance(interpretation, SetTitleCommandInterpretation)
        assert isinstance(interpretation.arguments, SetTitleCommandArguments)
        assert interpretation.command_type is CommandType.SET_TITLE
        assert interpretation.arguments.title == "Planification trimestrielle"

    @pytest.mark.parametrize("opening_fence", ["```json", "```"])
    def test_accepts_json_in_markdown_code_fence(
        self,
        opening_fence: str,
        fake_config: TranscribeConfig,
    ) -> None:
        """Common fenced model output is unwrapped before schema validation."""
        ai_manager = _ai_manager_with_response(
            f'{opening_fence}\n{{"command_type": "merge", "arguments": {{}}}}\n```',
            fake_config,
        )

        interpretation = ai_manager.interpret_command("merge this recording")

        assert interpretation.command_type is CommandType.MERGE

    def test_rejects_json_with_surrounding_explanation(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Fence tolerance does not allow additional prose around the payload."""
        ai_manager = _ai_manager_with_response(
            'Here is the result:\n```json\n{"command_type": "merge", "arguments": {}}\n```',
            fake_config,
        )

        with pytest.raises(UnexpectedChatClientAnswerException):
            ai_manager.interpret_command("merge this recording")

    def test_raises_on_unexpected_response(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """A response that is not a structured interpretation is rejected."""
        ai_manager = _ai_manager_with_response("GIBBERISH", fake_config)

        with pytest.raises(UnexpectedChatClientAnswerException):
            ai_manager.interpret_command("do the thing")

    @pytest.mark.parametrize(
        "response",
        [
            '{"command_type": "not-a-command", "arguments": {}}',
            '{"command_type": "merge", "arguments": {"unexpected": "value"}}',
            '{"command_type": "merge", "arguments": {"title": "Not allowed"}}',
            '{"command_type": "merge", "arguments": {"title": null}}',
            '{"command_type": "set_title", "arguments": {}}',
            '{"command_type": "set_title", "arguments": {"title": "   "}}',
        ],
    )
    def test_raises_on_invalid_structured_response(
        self,
        response: str,
        fake_config: TranscribeConfig,
    ) -> None:
        """Unknown types and unsupported arguments are rejected."""
        ai_manager = _ai_manager_with_response(response, fake_config)

        with pytest.raises(UnexpectedChatClientAnswerException):
            ai_manager.interpret_command("do the thing")
