"""Abstract AI manager for dependency injection."""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import override

from transcriber.clients.clients import AudioTranscriptionClient, ChatCompletionClient
from transcriber.commands.command_interpretation import CommandInterpretation
from transcriber.commands.command_registry import COMMAND_REGISTRY
from transcriber.config import TranscribeConfig
from transcriber.exception import (
    EmptyChatClientAnswerException,
    InvalidBundleNameAnswerException,
    UnexpectedChatClientAnswerException,
)
from transcriber.logger import logger

BUNDLE_NAME_MAX_LENGTH = 60  # arbitrary max length


def _unwrap_json_code_fence(response: str) -> str:
    """Remove one optional Markdown JSON fence from a model response."""
    stripped_response = response.strip()
    lines = stripped_response.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return stripped_response

    opening_fence = lines[0].strip().lower()
    if opening_fence not in {"```", "```json"}:
        return stripped_response

    return "\n".join(lines[1:-1]).strip()


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
    def interpret_command(self, command_string: str) -> CommandInterpretation:
        """Interpret a user command into a known type and structured arguments.

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
            raise InvalidBundleNameAnswerException(
                invalid_name=bundle_name,
                reason=f"length {len(bundle_name)} > {BUNDLE_NAME_MAX_LENGTH}",
                context="get_bundle_name_summary: LLM returned a bundle name too long",
            )

        return bundle_name

    @override
    def interpret_command(self, command_string: str) -> CommandInterpretation:
        """Interpret a user command into a known type and structured arguments.

        Uses the LLM to semantically match the command string to one of the
        registered command types. Only matches with high confidence; returns
        UNKNOWN if no clear match is found.

        Args:
            command_string: The raw command text to interpret.

        Returns:
            The matched command type and any arguments extracted for its handler.

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
  - Return exactly one JSON object with no markdown or surrounding explanation.
  - "command_type" must be the lowercase command value (for example, "merge").
  - "arguments" must be a JSON object. All currently available commands use an empty object.
  - Example: {{"command_type": "merge", "arguments": {{}}}}

  **User Command:**
  {command_string}

  **API-like Response:**"""

        raw_response = self.query_chat_completion(prompt)
        response = _unwrap_json_code_fence(raw_response)
        logger.debug(f"interpret_command answered {raw_response}")
        try:
            response_data: object = json.loads(response)
            return CommandInterpretation.model_validate(response_data)
        except (json.JSONDecodeError, ValueError) as error:
            logger.error(f"LLM returned unexpected response: {raw_response}")
            raise UnexpectedChatClientAnswerException(
                expected_type="structured command interpretation",
                actual_answer=raw_response,
                context=str(error),
            ) from error

    @override
    def extract_raw_commands(self, text: str, bundle_name: str) -> list[str]:
        """Extract raw commands from text, not including command boundaries.

        'Raw' means fully natural language and no command matching yet.
        Delegates to the injected text_client instead of doing HTTP directly.
        """
        logger.debug(f"AIManager Extracting commands for {bundle_name}")
        commands: list[str] = []
        prompt = f"""
You're part of an automated pipeline that transcribes personal audio recordings.

Your task is NOT to interpret the transcript.
Your task is ONLY to extract text delimited by spoken boundary phrases.

# Definitions

A command starts immediately after a spoken start boundary.

A command ends immediately before a spoken end boundary.

Start boundaries include equivalent phrases in any language, for example:
- start command
- begin command
- début commande
- commence commande

End or cancellation boundaries include equivalent phrases in any language, for example:
- stop command
- validate command
- fin commande
- valide commande
- cancel command
- annule commande

# Rules

- Extract the text exactly as spoken between the start and end boundaries.
- Do NOT include the boundary phrases themselves.
- Do NOT reword, summarize, translate, correct, or interpret the extracted text.
- The extracted text does NOT need to be a valid instruction. Any text between the boundaries is considered a command.
- If a cancellation boundary is encountered before an end boundary, discard the current command.
- If another start boundary is encountered before an end boundary, discard the current command.
- If the extracted text clearly contains multiple unrelated utterances or multiple complete sentences, discard it. Minor transcription errors or missing punctuation should not by themselves cause the command to be discarded.
- Ignore unmatched start or end boundaries.

# Output format

- Output one extracted command per line.
- Separate commands using Unix newlines (`\n`).
- If no valid commands are found, output exactly:
none
- Do not output any other text.

# Examples

Transcript:
"... début commande ouvre le garage fin commande ..."
Output:
ouvre le garage

Transcript:
"... start command continue previous recording stop command ..."
Output:
continue previous recording

Transcript:
"... début commande suite de l'enregistrement précédent fin commande ..."
Output:
suite de l'enregistrement précédent

Transcript:
"... début commande open the door annule commande ..."
Output:
none

Transcript:
"... start command first command stop command ... start command second command validate command ..."
Output:
first command
second command

Transcript:
{text}

Output:
        """
        response = self.query_chat_completion(prompt)
        lines = response.splitlines()
        if len(lines) == 1 and lines[0].lower() == "none":
            return []

        commands = [line.strip() for line in lines if line.strip() != ""]

        return commands
