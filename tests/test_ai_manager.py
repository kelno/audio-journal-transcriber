from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from transcriber.ai_manager import AIManager
from transcriber.commands.command_interpreter import extract_commands
from transcriber.config import TranscribeConfig

from .fake_clients import (
    FakeAudioClient,
    FakeChatClient,
    FakeChatClientWithErrors,
)


class TestAIManagerTranscription:
    """Tests for the transcribe_audio method."""

    def test_transcribe_audio_calls_client(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Test that transcribe_audio delegates to the audio client."""
        fake_transcribe = "Hello World"
        # Arrange: Create fake client and AI manager
        fake_audio_client = FakeAudioClient(response=fake_transcribe)
        fake_chat_client = FakeChatClient()
        ai_manager = AIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        test_audio_path = Path("/fake/test.mp3")

        # Act: Call transcribe_audio
        result = ai_manager.transcribe_audio(test_audio_path)

        # Assert: Check that the correct response was returned
        assert result == fake_transcribe

        # Assert: Check that we tracked the file being transcribed
        assert test_audio_path in fake_audio_client.transcribed_files


class TestAIManagerChatCompletion:
    """Tests for the query_chat_completion method."""

    def test_query_chat_completion_returns_response(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Test that query_chat_completion returns the client's response."""
        # Arrange
        fake_completion = "AI response here"
        fake_chat_client = FakeChatClient(response=fake_completion)
        fake_audio_client = FakeAudioClient()
        ai_manager = AIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        # Act
        result = ai_manager.query_chat_completion("Test prompt")

        # Assert
        assert result == fake_completion

    def test_query_chat_completion_tracks_messages(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Test that the chat client receives the correct message format."""
        # Arrange
        fake_chat_client = FakeChatClient(response="Response")
        fake_audio_client = FakeAudioClient()
        ai_manager = AIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        # Act
        ai_manager.query_chat_completion("My prompt")

        # Assert: Check the messages that were sent
        assert len(fake_chat_client.completion_calls) == 1
        messages = fake_chat_client.completion_calls[0]

        # Check structure
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "My prompt"

    def test_query_chat_completion_raises_on_empty_response(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Test that empty responses are handled correctly."""
        # Arrange
        fake_chat_client = FakeChatClient(response="")  # Empty response
        fake_audio_client = FakeAudioClient()
        ai_manager = AIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        # Act & Assert
        with pytest.raises(ValueError, match="Empty response"):
            ai_manager.query_chat_completion("Test prompt")


class TestAIManagerSummary:
    """Tests for the get_ai_summary method."""

    def test_get_ai_summary_returns_summary(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Test that get_ai_summary returns the chat response."""
        # Arrange
        expected_summary = """# Topics
Meeting about project roadmap

# Summary
We discussed the Q3 roadmap...

# Action items
- Create design docs
- Schedule follow-up"""

        fake_chat_client = FakeChatClient(response=expected_summary)
        fake_audio_client = FakeAudioClient()
        ai_manager = AIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        # Act
        result = ai_manager.get_ai_summary("transcript text here")

        # Assert
        assert result == expected_summary

    def test_get_ai_summary_includes_extra_context(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Test that extra_context is included in the prompt if provided."""
        # Arrange
        fake_config.text.extra_context = "Important: Be concise"
        fake_chat_client = FakeChatClient(response="Summary")
        fake_audio_client = FakeAudioClient()
        ai_manager = AIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        # Act
        ai_manager.get_ai_summary("transcript")

        # Assert: Check that extra_context was included in the prompt
        messages = fake_chat_client.completion_calls[0]
        prompt = messages[1]["content"]
        assert "Important: Be concise" in prompt


class TestAIManagerBundleName:
    """Tests for the get_bundle_name_summary method."""

    def test_get_bundle_name_summary_returns_name(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Test that get_bundle_name_summary returns the chat response."""
        # Arrange
        fake_response = "Meeting notes"
        fake_chat_client = FakeChatClient(response=fake_response)
        fake_audio_client = FakeAudioClient()
        ai_manager = AIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        # Act
        result = ai_manager.get_bundle_name_summary("summary text")

        # Assert
        assert result == fake_response

    def test_get_bundle_name_summary_sanitizes_output(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Test that non-alphanumeric characters are removed."""
        # Arrange
        fake_chat_client = FakeChatClient(
            response="Meeting@#$%notes!@#$",  # Contains invalid characters
        )
        fake_audio_client = FakeAudioClient()
        ai_manager = AIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        # Act
        result = ai_manager.get_bundle_name_summary("summary")

        # Assert: Only alphanumeric and spaces should remain
        assert result == "Meetingnotes"

    def test_get_bundle_name_summary_raises_if_too_long(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Test that overly long names raise an error."""
        # Arrange
        fake_chat_client = FakeChatClient(
            response="This is a very long bundle name that exceeds the maximum" * 3,
        )
        fake_audio_client = FakeAudioClient()
        ai_manager = AIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        # Act & Assert
        with pytest.raises(ValueError, match="too long"):
            ai_manager.get_bundle_name_summary("summary")

    def test_get_bundle_name_summary_fails_on_api_error(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Test that API errors are propagated."""
        # Arrange
        fake_chat_client = FakeChatClientWithErrors()
        fake_audio_client = FakeAudioClient()
        ai_manager = AIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        # Act & Assert
        with pytest.raises(ValueError, match=FakeChatClientWithErrors.ERROR_MESSAGE):
            ai_manager.get_bundle_name_summary("summary")

    def test_extract_commands_no_commands(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Test extract_commands function."""
        # Arrange
        fake_chat_client = FakeChatClient("none")
        fake_audio_client = FakeAudioClient()
        ai_manager = AIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        commands = extract_commands(
            "Whatever text without command",
            "TestBundle",
            ai_manager,
        )
        assert commands == []

    def test_extract_commands_with_commands(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Test extract_commands function.

        The main breaking point of this function is still what the AI returns.
        This test does not cover that part and only makes sure extract_commands returns the data in the correct format.
        """
        command1 = "Eat a sandwich"
        command2 = "Do a backflip"

        # Arrange
        fake_chat_client = FakeChatClient(f"{command1}\n{command2}")
        fake_audio_client = FakeAudioClient()
        ai_manager = AIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        commands = extract_commands(
            f"""Hello, start command {command1} end command.
            Then the next day I did enjoy the sun. start command {command2} end commend.
            That's it for today!""",
            "TestBundle",
            ai_manager,
        )
        assert commands == [command1, command2]
