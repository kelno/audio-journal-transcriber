from pathlib import Path

import pytest

from transcriber.ai_manager import RealAIManager
from transcriber.bundle_title import BUNDLE_TITLE_MAX_LENGTH
from transcriber.config import TranscribeConfig
from transcriber.exception import EmptyChatClientAnswerException, InvalidBundleTitleException

from .fake_clients import FakeAudioClient, FakeChatClient


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
        ai_manager = RealAIManager(
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
        ai_manager = RealAIManager(
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
        ai_manager = RealAIManager(
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
        ai_manager = RealAIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        # Act & Assert
        with pytest.raises(EmptyChatClientAnswerException, match="Empty response"):
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
        ai_manager = RealAIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        # Act
        result = ai_manager.get_ai_summary("transcript text here")

        # Assert
        assert result == expected_summary

    @pytest.mark.parametrize("opening_fence", ["```markdown", "```"])
    def test_get_ai_summary_unwraps_markdown_code_fence(
        self,
        opening_fence: str,
        fake_config: TranscribeConfig,
    ) -> None:
        """A fence enclosing the whole model response is omitted from the summary."""
        expected_summary = "# Topics\nProject planning\n\n# Summary\nRoadmap discussion"
        fake_chat_client = FakeChatClient(
            response=f"{opening_fence}\n{expected_summary}\n```",
        )
        ai_manager = RealAIManager(
            audio_client=FakeAudioClient(),
            chat_client=fake_chat_client,
            config=fake_config,
        )

        result = ai_manager.get_ai_summary("transcript text here")

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
        ai_manager = RealAIManager(
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

    def test_get_ai_summary_separates_global_and_recording_context(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Global and recording-specific context appear in distinct labeled blocks."""
        fake_config.text.extra_context = "Operator note: always be formal"
        fake_chat_client = FakeChatClient(response="Summary")
        fake_audio_client = FakeAudioClient()
        ai_manager = RealAIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        ai_manager.get_ai_summary(
            "transcript",
            record_context="This is a recording of me trying to convince my wife pigeons are the best birds.",
        )

        prompt = fake_chat_client.completion_calls[0][1]["content"]
        # Both contexts are present and clearly labeled/distinguishable.
        assert "<operator_global_context>" in prompt
        assert "Operator note: always be formal" in prompt
        assert "<recording_specific_context>" in prompt
        assert "This is a recording of me trying to convince my wife pigeons are the best birds." in prompt


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
        ai_manager = RealAIManager(
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
        """Test that filesystem-invalid characters are replaced and whitespace collapses."""
        # Arrange
        fake_chat_client = FakeChatClient(
            response='  Meeting<>:"/\\|?*   notes  ',
        )
        fake_audio_client = FakeAudioClient()
        ai_manager = RealAIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        # Act
        result = ai_manager.get_bundle_name_summary("summary")

        assert result == "Meeting notes"

    def test_get_bundle_name_summary_truncates_long_title_at_word_boundary(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Overly long titles are shortened deterministically without splitting a word."""
        # Arrange
        fake_chat_client = FakeChatClient(
            response="This is a very long bundle title that exceeds the maximum allowed character length",
        )
        fake_audio_client = FakeAudioClient()
        ai_manager = RealAIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        result = ai_manager.get_bundle_name_summary("summary")

        assert result == "This is a very long bundle title that exceeds the maximum"
        assert len(result) <= BUNDLE_TITLE_MAX_LENGTH

    def test_get_bundle_name_summary_rejects_empty_normalized_title(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """A response containing only invalid filename characters is rejected."""
        ai_manager = RealAIManager(
            audio_client=FakeAudioClient(),
            chat_client=FakeChatClient(response='<>:"/\\|?*'),
            config=fake_config,
        )

        with pytest.raises(InvalidBundleTitleException, match="empty"):
            ai_manager.get_bundle_name_summary("summary")

    def test_extract_commands_no_commands(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Test extract_commands function."""
        # Arrange
        fake_chat_client = FakeChatClient("none")
        fake_audio_client = FakeAudioClient()
        ai_manager = RealAIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        commands = ai_manager.extract_raw_commands(
            "Whatever text without command",
            "TestBundle",
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
        ai_manager = RealAIManager(
            audio_client=fake_audio_client,
            chat_client=fake_chat_client,
            config=fake_config,
        )

        commands = ai_manager.extract_raw_commands(
            f"""Hello, start command {command1} end command.
            Then the next day I did enjoy the sun. start command {command2} end commend.
            That's it for today!""",
            "TestBundle",
        )
        assert commands == [command1, command2]
