"""Fake AI manager for testing without real model calls."""

from pathlib import Path
from typing import override

import pytest

from transcriber.ai_manager import AIManager
from transcriber.commands.command_interpretation import ArgumentlessCommandInterpretation, CommandInterpretation
from transcriber.commands.command_type import CommandType


class FakeAIManager(AIManager):
    """In-memory AI manager returning configurable canned responses.

    Each capability has a default response that can be overridden per instance
    via constructor arguments, so tests can drive specific behavior without any
    real API access.
    """

    # Canned responses (overridable per instance via the constructor).
    # for transcribe_audio()
    transcript: str
    # for query_chat_completion()
    chat_completion: str
    # for get_ai_summary()
    summary: str
    # for get_bundle_name_summary()
    bundle_name: str
    # [query string, matched type] for interpret_command()
    interpret_commands: dict[str, CommandInterpretation]
    # [query string, list of answers] for extract_raw_commands()
    raw_commands: dict[str, list[str]]

    def __init__(
        self,
        transcript: str = "This is a test transcription.",
        chat_completion: str = "Test response",
        summary: str = "Test response",
        bundle_name: str = "Test response",
        # lambda constructor syntax to avoid mypy error
        interpret_commands: dict[str, CommandInterpretation] | None = None,
        raw_commands: dict[str, list[str]] | None = None,
    ) -> None:
        """Initialize the fake AI manager with canned responses.

        Args:
            transcript: Value returned by transcribe_audio().
            chat_completion: Value returned by query_chat_completion().
            summary: Value returned by get_ai_summary().
            bundle_name: Value returned by get_bundle_name_summary().
            interpret_commands: Values returned by interpret_command().
            raw_commands: Values returned by extract_raw_commands().

        """
        super().__init__()
        self.transcript = transcript
        self.chat_completion = chat_completion
        self.summary = summary
        self.bundle_name = bundle_name
        self.interpret_commands = {} if interpret_commands is None else interpret_commands
        self.raw_commands = raw_commands if raw_commands is not None else {}

        # Track calls for verification in tests.
        self.transcribed_files: list[Path] = []
        self.chat_prompts: list[str] = []
        self.summarized_transcripts: list[str] = []
        self.named_summaries: list[str] = []
        self.interpreted_commands: list[str] = []
        self.extracted_texts: list[tuple[str, str]] = []

    @override
    def transcribe_audio(self, audio_path: Path) -> str:
        """Return the canned transcript, tracking which files were transcribed."""
        self.transcribed_files.append(audio_path)
        return self.transcript

    @override
    def query_chat_completion(self, prompt: str) -> str:
        """Return the canned chat completion, tracking the prompt."""
        self.chat_prompts.append(prompt)
        return self.chat_completion

    @override
    def get_ai_summary(
        self,
        transcript: str,
        record_context: str | None = None,
    ) -> str:
        """Return the canned summary, tracking the transcript."""
        self.summarized_transcripts.append(transcript)
        return self.summary

    @override
    def get_bundle_name_summary(self, summary: str) -> str:
        """Return the canned bundle name, tracking the summary."""
        self.named_summaries.append(summary)
        return self.bundle_name

    @override
    def interpret_command(self, command_string: str) -> CommandInterpretation:
        """Return canned command interpretations, tracking the command string.

        Try to match with commands registered in interpret_commands, else returns UNKNOWN.
        """
        self.interpreted_commands.append(command_string)
        if command_string in self.interpret_commands:
            return self.interpret_commands[command_string]
        else:
            return ArgumentlessCommandInterpretation(command_type=CommandType.UNKNOWN)

    @override
    def extract_raw_commands(self, text: str, bundle_name: str) -> list[str]:
        """Return the canned raw commands, tracking the inputs."""
        self.extracted_texts.append((text, bundle_name))
        if text in self.raw_commands:
            return self.raw_commands[text]
        else:
            return []


@pytest.fixture
def fake_ai_manager() -> FakeAIManager:
    """Create a fresh fake AI manager for each test."""
    return FakeAIManager()
