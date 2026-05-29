# pyright: reportAny=false, reportArgumentType=false

import json
from pathlib import Path
from typing import final, override
from urllib.parse import urljoin

import requests
from openai import OpenAI

from transcriber.config import AudioConfig, TextConfig
from transcriber.logger import logger

from .clients import AudioTranscriptionClient, ChatCompletionClient

# HTTP success status code
HTTP_OK = 200


@final
class OpenAIAudioClient(AudioTranscriptionClient):
    """Real implementation for OpenAI audio transcription API."""

    def __init__(self, config: AudioConfig):
        """Initialize with audio config.

        Args:
            config: AudioConfig with api_base_url, model, api_key, stream settings.

        """
        self.config = config

    @override
    def transcribe(self, audio_path: Path) -> str:
        """Transcribe audio file using OpenAI-compatible API with streaming."""
        logger.debug(f"Transcribing: {audio_path}")

        with Path.open(audio_path, "rb") as audio_file:
            files = {
                "file": (
                    audio_path.name,
                    audio_file,
                    "multipart/form-data",
                ),
            }
            data = {
                "model": self.config.model,
                "stream": "true" if self.config.stream else "false",
            }
            url = urljoin(self.config.api_base_url, "audio/transcriptions")
            response = requests.post(
                url=url,
                files=files,
                data=data,
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                stream=True,
                timeout=(60 if self.config.stream else 600),
            )

        if response.status_code == HTTP_OK:
            return self._extract_streaming_response(response)
        else:
            msg = f"Transcription failed with status code {response.status_code}: {response.text}"
            raise ValueError(msg)

    def _extract_streaming_response(self, response: requests.Response) -> str:
        """Process a streaming response from the transcription API.

        Returns:
            str: The complete transcript.

        """
        logger.debug("Processing streaming response")
        text_chunks: list[str] = []
        print_done = False

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
                        if not print_done:
                            logger.debug(f"Transcript streaming start: {text} [...]")
                except Exception as e:
                    logger.error(f"Error decoding line:\n{line}\n{e}")
                    raise

        complete_transcript = " ".join(text_chunks)
        return complete_transcript


@final
class OpenAIChatClient(ChatCompletionClient):
    """Real implementation for OpenAI chat completion API."""

    def __init__(self, config: TextConfig):
        """Initialize with text config.

        Args:
            config: TextConfig with api_base_url, model, api_key.

        """
        self.config = config

    @override
    def create_completion(self, messages: list[dict[str, str]]) -> str:
        """Generate a chat completion using OpenAI API."""
        client = OpenAI(
            base_url=self.config.api_base_url,
            api_key=self.config.api_key,
        )
        completion = client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            extra_body={
                "parent_id": None,
            },  # Fixes an issue with OpenWebUI 0.9.5, until https://github.com/open-webui/open-webui/commit/bc244fdc90504824b76654880898bf3f6649c299 is published
        )

        if len(completion.choices) == 0:
            msg = f"No choices returned: {completion}"
            logger.error(msg)
            raise ValueError(msg)

        content = completion.choices[0].message.content
        if not content:
            msg = f"Empty content returned: {completion}"
            logger.error(msg)
            raise ValueError(msg)

        return content
