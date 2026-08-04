# pyright: reportAny=false, reportArgumentType=false

import json
import traceback
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
        super().__init__()
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
        """Process an OpenAI-compatible SSE transcription response.

        Returns:
            str: The complete transcript.

        """
        logger.debug("Processing streaming response")
        response.raise_for_status()

        text_chunks: list[str] = []
        final_text: str | None = None
        print_done = False

        for raw_line in response.iter_lines():
            if not raw_line:
                continue

            try:
                line = raw_line.decode("utf-8").strip()

                # SSE comments are used as connection keepalives.
                if line.startswith(":"):
                    continue

                # Ignore other SSE fields we do not use, such as event: or id:.
                if not line.startswith("data:"):
                    continue

                json_str = line.removeprefix("data:").lstrip()
                if json_str == "[DONE]":
                    break

                event = json.loads(json_str)

                if error := event.get("error"):
                    message = error.get("message", "Transcription stream failed")
                    raise RuntimeError(message)

                match event.get("type"):
                    case "transcript.text.delta":
                        delta = event.get("delta", "")
                        if delta:
                            text_chunks.append(delta)
                            if not print_done:
                                logger.debug(
                                    "Transcript streaming start: %s [...]",
                                    delta,
                                )
                                print_done = True

                    case "transcript.text.done":
                        final_text = event.get("text", "")
                        break

                    case _:
                        # Compatibility with older backends that streamed {"text": ...}.
                        if text := event.get("text"):
                            text_chunks.append(text)

            except Exception:
                logger.error(
                    "Error decoding line:\n%s\n%s",
                    raw_line,
                    traceback.format_exc(),
                )
                raise

        # Deltas already contain their required leading spaces, so don't join with " ".
        return final_text if final_text is not None else "".join(text_chunks)


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
