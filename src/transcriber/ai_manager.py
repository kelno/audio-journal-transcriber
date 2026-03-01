from pathlib import Path
from dataclasses import dataclass
import json

from urllib.parse import urljoin
import requests
from openai import OpenAI

from transcriber.config import TranscribeConfig
from .logger import logger


@dataclass
class AudioConfigOptions:
    """Intermediate object for audio configuration options."""

    model: str
    stream: bool
    api_base_url: str
    api_key: str

    def __str__(self):
        return f"AudioConfigOptions(model={self.model}, stream={self.stream}, api_base_url={self.api_base_url}, api_key={'***' if self.api_key else 'None'})"


@dataclass
class TextConfigOptions:
    """Intermediate object for text configuration options."""

    model: str
    api_base_url: str
    api_key: str
    extra_context: str | None

    def __str__(self):
        return f"TextConfigOptions(model={self.model}, api_base_url={self.api_base_url}, api_key={'***' if self.api_key else 'None'}, extra_context={self.extra_context})"


@dataclass
class AIManager:
    """Manages interactions with AI models for transcription and summarization."""

    config: TranscribeConfig

    def __post_init__(self):
        """Initialize intermediate configuration objects."""
        self.audio_config = AudioConfigOptions(
            model=self.config.audio.model,
            stream=self.config.audio.stream,
            api_base_url=self.config.audio.api_base_url,
            api_key=self.config.audio.api_key,
        )
        self.text_config = TextConfigOptions(
            model=self.config.text.model,
            api_base_url=self.config.text.api_base_url,
            api_key=self.config.text.api_key,
            extra_context=self.config.text.extra_context,
        )

        # Debug print for configuration
        logger.debug(f"Audio configuration: {self.audio_config}")
        logger.debug(f"Text configuration: {self.text_config}")

    def transcribe_audio(self, audio_path: Path) -> str:
        """
        Transcribe an audio file using a local OpenAI-compatible API with streaming.
        Writes output directly to file.
        """
        logger.debug(f"AIManager Transcribing: {audio_path}")

        with open(audio_path, "rb") as audio_file:
            files = {
                "file": (
                    audio_path.name,
                    audio_file,
                    "multipart/form-data",
                )
            }
            data = {
                "model": self.audio_config.model,
                "stream": "true" if self.audio_config.stream else "false",
            }
            url = urljoin(self.audio_config.api_base_url, "audio/transcriptions")
            response = requests.post(
                url=url,
                files=files,
                data=data,
                headers={"Authorization": f"Bearer {self.audio_config.api_key}"},
                stream=True,  # post option, delay body parsing
                timeout=(60 if self.audio_config.stream else 600),  # 1 min for streaming, 10 min for non-streaming
            )

        if response.status_code == 200:
            return self.extract_streaming_response(response)
        else:
            raise ValueError(f"Transcription failed with status code {response.status_code} and response: {response.text}")

    def extract_streaming_response(self, response) -> str:
        """Process a streaming response from the transcription API and write directly to file.
        Returns the complete transcript as a string."""
        logger.debug("Processing streaming response")
        text_chunks = []

        for line in response.iter_lines():
            if line:
                try:
                    json_str = line.decode("utf-8").removeprefix("data: ")
                    if json_str.strip() == "[DONE]":
                        break
                    result = json.loads(json_str)
                    if "text" in result:
                        text = result["text"]
                        text_chunks.append(text)
                        print(text, end="", flush=True)
                except Exception as e:
                    logger.error(f"Error decoding line:\n{line}")
                    raise e

        print("\n")
        complete_transcript = " ".join(text_chunks)

        return complete_transcript

    def query_chat_completion(self, prompt: str) -> str:
        """
        Returns the output string on success, or None on failure.
        Raises:
            ValueError: When OpenAI client returns an invalid answer.
            *: Pass through any exceptions from the OpenAI client.
        """

        client = OpenAI(base_url=self.text_config.api_base_url, api_key=self.text_config.api_key)

        # https://platform.openai.com/docs/api-reference/chat/create
        completion = client.chat.completions.create(
            model=self.text_config.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are part of an automated pipeline to transcribe and summarize texts.",
                },
                {"role": "user", "content": prompt},
            ],
        )

        if len(completion.choices) == 0:
            logger.error("query_chat_completion failed: no choices returned")
            raise ValueError("query_chat_completion failed: no choices returned")

        completion = completion.choices[0].message.content
        if not completion:
            logger.error("query_chat_completion failed: empty content")
            raise ValueError("query_chat_completion failed: empty content")

        return completion

    def get_ai_summary(self, transcript: str) -> str:
        """
        Generate AI summary from transcript
        Raises:
            *: Pass through any exceptions from the OpenAI client.
        """

        extra_context_prompt = f"Some extra context:\n{self.text_config.extra_context}" if self.text_config.extra_context is not None else ""
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
            [List of actionable points only if clearly stated in the transcript; otherwise write “(None)”]
            ```
            {extra_context_prompt}
            ---
            Transcript:
            {transcript}
        """
        summary = self.query_chat_completion(prompt)
        logger.debug(f"get_ai_summary succeeded. Excerpt: {summary[:160]}...")
        return summary

    def get_bundle_name_summary(self, summary: str) -> str:
        """
        Returns a short AI generated name for a bundle
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
        # Ai tends to add quotes around the name
        bundle_name = bundle_name.strip(" \n\"'")

        logger.debug(f"AI generated bundle name: {bundle_name}")
        if len(bundle_name) > 60:  # arbitrary max length
            raise ValueError(f"get_bundle_name_summary: LLM returned a bundle name too long: {bundle_name}")

        return bundle_name
