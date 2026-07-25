"""Abstract AI manager for dependency injection."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import override

from transcriber.clients.clients import AudioTranscriptionClient, ChatCompletionClient
from transcriber.commands.command_registry import COMMAND_REGISTRY
from transcriber.commands.command_type import CommandType
from transcriber.config import TranscribeConfig
from transcriber.exception import (
    EmptyChatClientAnswerException,
    InvalidBundleNameAnswerException,
    UnexpectedChatClientAnswerException,
)
from transcriber.logger import logger

BUNDLE_NAME_MAX_LENGTH = 60  # arbitrary max length


class AIManager(ABC):
    """Abstract interface for AI model interactions (transcription, summarization)."""

    @abstractmethod
    def transcribe_audio(self, audio_path: Path) -> str:
        """Transcribe an audio file and return the transcript text."""
        ...

    @abstractmethod
    def query_chat_completion(self, prompt: str) -> str:
        """Query the chat completion API with the given prompt.

        Raises:
            ValueError: When client returns an invalid answer.

        """
        ...

    @abstractmethod
    def get_ai_summary(
        self,
        transcript: str,
        record_context: str | None = None,
    ) -> str:
        """Generate an AI summary from a transcript."""
        ...

    @abstractmethod
    def get_bundle_name_summary(self, summary: str) -> str:
        """Return a short AI generated name for a bundle.

        Raises:
            InvalidBundleNameAnswerException: When LLM returns an invalid bundle name.

        """
        ...

    @abstractmethod
    def interpret_command(self, command_string: str) -> CommandType:
        """Interpret a user command and match it to a known command type.

        Raises:
            UnexpectedChatClientAnswerException: When the LLM returns an invalid or unparseable response.

        """
        ...

    @abstractmethod
    def extract_raw_commands(self, text: str, bundle_name: str) -> list[str]:
        """Extract raw (natural language) commands from text. Not including command boundaries."""
        ...


@dataclass
class RealAIManager(AIManager):
    """Manages interactions with AI models for transcription and summarization."""

    audio_client: AudioTranscriptionClient
    chat_client: ChatCompletionClient
    config: TranscribeConfig

    def __post_init__(self):
        """Initialize logging after dataclass fields are set."""
        logger.debug("AIManager initialized")
        logger.debug(f"Audio configuration: {self.config.audio}")
        logger.debug(f"Text configuration: {self.config.text}")

    @override
    def transcribe_audio(self, audio_path: Path) -> str:
        """Transcribe an audio file.

        Delegates to the injected audio_client instead of doing HTTP directly.
        """
        logger.debug(f"AIManager Transcribing: {audio_path}")
        return self.audio_client.transcribe(audio_path)

    @override
    def query_chat_completion(self, prompt: str) -> str:
        """Query the chat completion API with the given prompt.

        Delegates to the injected chat_client instead of doing HTTP directly.

        Raises:
            ValueError: When client returns an invalid answer.

        """
        messages = [
            {
                "role": "system",
                "content": "You are part of an automated pipeline to transcribe and summarize texts.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        response = self.chat_client.create_completion(messages)

        if not response:
            msg = "Empty response from chat client"
            logger.error(msg)
            raise EmptyChatClientAnswerException(msg)

        return response

    @override
    def get_ai_summary(
        self,
        transcript: str,
        record_context: str | None = None,
    ) -> str:
        """Generate AI summary from transcript.

        Args:
            transcript: The transcript text to summarize.
            record_context: Optional per-recording context provided by the user
                (e.g. who a message is addressed to). It is kept distinct from the
                operator-level global context so the model knows which is which.

        Raises:
            *: Pass through any exceptions from the OpenAI client.

        """
        global_context = self.config.text.extra_context or "None provided."
        recording_context = record_context or "None provided."
        prompt = f"""
            You are part of an automated pipeline that transcribes personal audio recordings and summarizes them.
            Your task: Summarize the input transcript and output a structured markdown file.

            **Context to help you summarize**
            The transcript is a raw spoken recording and may lack context (for example, who a
            message is addressed to, or the broader situation). Use the sections below to resolve
            ambiguity and fill gaps. Never invent facts that contradict the transcript; if the
            context conflicts with what was actually said, follow the transcript and note the conflict.

            <operator_global_context>
            {global_context}
            </operator_global_context>

            <recording_specific_context>
            {recording_context}
            </recording_specific_context>

            **Instructions**
            - Detect the language of the transcript and **write the entire content (except section titles) in that same language**.
            - For example, if the transcript is in French, **all sentences and summaries must be in French**, not English.
            - Only the section titles ("# Topics", "# Summary", "# Action items") stay in English.
            - Because this is a spoken transcript, it may contain transcription errors or unclear sections.
            When you make assumptions about unclear words or phrases, **explicitly note them** and describe your reasoning briefly.
            - Do **not** include any markdown code fences (```).
            - Follow **exactly** this structure in your output:
            ```
            # Topics
            [List of topics mentioned in the recording]
            # Summary
            [Comprehensive summary in natural language]
            # Action items
            [List of actionable points only if clearly stated in the transcript; otherwise write "(None)"]
            ```
            ---
            Transcript:
            {transcript}
        """
        summary = self.query_chat_completion(prompt)
        logger.debug(f"get_ai_summary succeeded. Excerpt: {summary[:160]} [...]]")
        return summary

    @override
    def get_bundle_name_summary(self, summary: str) -> str:
        """Return a short AI generated name for a bundle.

        Raises:
            InvalidBundleNameAnswerException: When LLM returns an invalid bundle name.
            *: Pass through any exceptions from the OpenAI client.

        """
        prompt = f"""
            You are part of an automated pipeline to transcribe and summarize texts.
            - You should act as a function and only return a very short summary intended for file naming, max 6 words.
            - The text should be synthetic, such as "Test microphone recording" rather than "Testing a recording with the microphone".
            - The text needs to be valid under NTFS and EXT4.
            - It should be written in natural langage, such as "Microphone recording test" or "Experimenting with painting".
            - Answer using the same language as the transcript. For example if the transcript is in french, answer in french.
            Okay. Now the summary follows:
            ---
            {summary}"""
        bundle_name = self.query_chat_completion(prompt)

        # Sanitize (Source: https://stackoverflow.com/a/7406369)
        bundle_name = "".join(c for c in bundle_name if c.isalpha() or c.isdigit() or c == " ").rstrip()

        logger.debug(f"AI generated bundle name: {bundle_name}")
        if len(bundle_name) > BUNDLE_NAME_MAX_LENGTH:
            msg = f"get_bundle_name_summary: LLM returned a bundle name too long: {bundle_name}"
            raise InvalidBundleNameAnswerException(msg)

        return bundle_name

    @override
    def interpret_command(self, command_string: str) -> CommandType:
        """Interpret a user command and match it to a known command type.

        Uses the LLM to semantically match the command string to one of the
        registered command types. Only matches with high confidence; returns
        UNKNOWN if no clear match is found.

        Args:
            command_string: The raw command text to interpret.

        Returns:
            The matched CommandType, or CommandType.UNKNOWN if no confident match.

        Raises:
            UnexpectedChatClientAnswerException: When the LLM returns an invalid or unparseable response.

        """
        logger.debug(f"Interpreting command: {command_string}")

        # Build the command registry description for the prompt
        command_descriptions = "\n".join(
            f"- {meta.command_type.value.upper()}: {meta.description} (aliases: {', '.join(meta.aliases or [])})"
            for meta in COMMAND_REGISTRY.values()
        )

        prompt = f"""
  You are a command matcher in an automated pipeline, you act as an API. Your task is to match a natural language user command to a predefined command type.

  **Available Commands:**
  {command_descriptions}

  **Matching Instructions:**
  - Only match a command if you are VERY CONFIDENT it maps to one of the available commands.
  - Consider both the exact wording and semantic meaning.
  - The commands can be given in any language, not only english.
  - If the command is ambiguous or doesn't clearly match any known command, use the UNKNOWN
  command type.

  **Response format:**
  - First line: The command type (e.g., "MERGE", "DELETE", ...), only the string with no other text or quotes, in uppercase.
  - Second line: Always empty
  - Third line: A short explanation of your choice, regular case.
  Lines are seperated by the regular unix line return \n (backslash n).

  **User Command:**
  {command_string}

  **API-like Response:**"""

        response = self.query_chat_completion(prompt).strip("\"'` ").upper()
        logger.debug(f"interpret_command answered {response}")
        # Try to match the response to a CommandType
        for cmd_type in CommandType:
            if response.split("\n")[0] == cmd_type.value.upper():
                return cmd_type

        # If no match and response is "UNKNOWN", return UNKNOWN
        if response == "UNKNOWN":
            return CommandType.UNKNOWN

        # Invalid response
        msg = f"LLM returned unexpected response: {response}"
        logger.error(msg)
        raise UnexpectedChatClientAnswerException(msg)

    @override
    def extract_raw_commands(self, text: str, bundle_name: str) -> list[str]:
        """Extract raw commands from text, not including command boundaries.

        'Raw' means fully natural language and no command matching yet.
        Delegates to the injected text_client instead of doing HTTP directly.
        """
        logger.debug(f"AIManager Extracting commands for {bundle_name}")
        commands: list[str] = []
        prompt = f"""
        You are part of an automated pipeline that transcribes personal audio recordings and summarizes them.
        Your task: You act as a function to extract vocal commands given by the user in the following transcripts.
        Return format:
            - One command per line, separated by unix newlines
            - If no commands are found, return the exact 4 characters string "none"
            - Do not include any other text or delimiters or special formatting in the response
        Vocal commands are defined as follows:
            - It starts when the user says "start command"
            - It ends with "stop command" or "validate command"
            - A user can also say "cancel command", in this case just ignore the command
            - Words can be translated + or be out of order. "fin commande" is valid, as well as "command stop"
            - The command is whatever the user says between those two (so not including those boundaries), without any extra processing
            - If the command is more than one sentence, something is wrong in the transcript and the command should be skipped.


        Transcript: {text}
        """
        response = self.query_chat_completion(prompt)
        lines = response.splitlines()
        if len(lines) == 1 and lines[0] == "none":
            return []

        commands = [line.strip() for line in lines if line.strip() != ""]

        return commands
