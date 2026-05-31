from dataclasses import dataclass
from pathlib import Path

from transcriber.clients.clients import AudioTranscriptionClient, ChatCompletionClient
from transcriber.config import TranscribeConfig
from transcriber.logger import logger

BUNDLE_NAME_MAX_LENGTH = 60  # arbitrary max length


@dataclass
class AIManager:
    """Manages interactions with AI models for transcription and summarization."""

    audio_client: AudioTranscriptionClient
    chat_client: ChatCompletionClient
    config: TranscribeConfig

    def __post_init__(self):
        """Initialize logging after dataclass fields are set."""
        logger.debug("AIManager initialized")
        logger.debug(f"Audio configuration: {self.config.audio}")
        logger.debug(f"Text configuration: {self.config.text}")

    def transcribe_audio(self, audio_path: Path) -> str:
        """Transcribe an audio file.

        Delegates to the injected audio_client instead of doing HTTP directly.
        """
        logger.debug(f"AIManager Transcribing: {audio_path}")
        return self.audio_client.transcribe(audio_path)

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
            raise ValueError(msg)

        return response

    def get_ai_summary(self, transcript: str) -> str:
        """Generate AI summary from transcript.

        Raises:
            *: Pass through any exceptions from the OpenAI client.

        """
        extra_context_prompt = (
            f"Some extra context:\n{self.config.text.extra_context}" if self.config.text.extra_context is not None else ""
        )
        prompt = f"""
            You are part of an automated pipeline that transcribes personal audio recordings and summarizes them.
            Your task: Summarize the input transcript and output a structured markdown file.

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
            {extra_context_prompt}
            ---
            Transcript:
            {transcript}
        """
        summary = self.query_chat_completion(prompt)
        logger.debug(f"get_ai_summary succeeded. Excerpt: {summary[:160]} [...]")
        return summary

    def get_bundle_name_summary(self, summary: str) -> str:
        """Return a short AI generated name for a bundle.

        Raises:
            ValueError: When LLM returns an invalid bundle name.
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
            raise ValueError(msg)

        return bundle_name
