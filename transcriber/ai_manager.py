from pathlib import Path
from dataclasses import dataclass
import json

from urllib.parse import urljoin
import requests
from openai import OpenAI

from transcriber.config import TranscribeConfig
from transcriber.logger import get_logger

logger = get_logger()


@dataclass
class AIManager:
    """Manages interactions with AI models for transcription and summarization."""

    config: TranscribeConfig

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
                "model": self.config.audio.model,
                "stream": "true" if self.config.audio.stream else "false",
            }
            url = urljoin(self.config.audio.api_base_url, "audio/transcriptions")
            response = requests.post(
                url=url,
                files=files,
                data=data,
                headers={"Authorization": f"Bearer {self.config.audio.api_key}"},
                stream=True,
                timeout=(
                    60 if self.config.audio.stream else 600
                ),  # 1 min for streaming, 10 min for non-streaming
            )

        if response.status_code == 200:
            return self.extract_streaming_response(response)
        else:
            raise ValueError(
                f"Transcription failed with status code {response.status_code} and response: {response.text}"
            )

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

        client = OpenAI(
            base_url="http://localhost:8080/api/", api_key=self.config.text.api_key
        )

        # https://platform.openai.com/docs/api-reference/chat/create
        completion = client.chat.completions.create(
            model=self.config.text.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are part of an automated pipeline to transcribe and summarize texts.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        if len(completion.choices) == 0:
            logger.error("AI summary failed: no choices returned")
            raise ValueError("AI summary failed: no choices returned")

        completion = completion.choices[0].message.content
        if not completion:
            logger.error("AI summary failed: empty content")
            raise ValueError("AI summary failed: empty content")

        return completion

    def get_ai_summary(self, transcript: str) -> str:
        """
        Generate AI summary from transcript
        Raises:
            *: Pass through any exceptions from the OpenAI client.
        """

        try:
            extra_context_prompt = (
                f"Extra context:\n{self.config.text.extra_context}"
                if self.config.text.extra_context is not None
                else ""
            )
            prompt = f"""
                You are part of an automated pipeline to transcribe and summarize texts, in a markdown format. 
                Please summarize the following transcript of my own audio recording. 
                Extra instructions:
                - Answer using the same language as the transcript. For example if the transcript is in french, answer in french.
                - List of topics: There should be a list of topics mentioned in the recording with the title "Topics".
                - Action items: If and only if the transcript very specifically mentions things that should be done, highlight those points as a recap at the end of the summary with the title "Action Items".
                - Given that this is an audio transcript that might be of poor quality, you might need to make some assumptions as to what was said. In those instances, take extra care to announce your assumptions clearly about what words were wrongly transcribed. 
                {extra_context_prompt}
                Okay. Now the transcript follows:
                ---
                {transcript}"""
            summary = self.query_chat_completion(prompt)
            logger.debug(f"AI summary succeeded. Excerpt: {summary[:160]}...")
            return summary
        except Exception:
            logger.error("AI summary failed with exception")
            raise

    def get_bundle_name_summary(self, summary: str) -> str:
        """
        Returns a short AI generated name for a bundle
        Raises:
            ValueError: When LLM returns an invalid bundle name.
            *: Pass through any exceptions from the OpenAI client.
        """

        try:

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
            logger.debug(f"AI generated bundle name: {bundle_name}")
            if len(bundle_name) > 60:  # arbitrary max length
                raise ValueError(f"LLM returned a bundle name too long: {bundle_name}")

            return bundle_name
        except Exception:
            logger.error("AI summary failed with exception")
            raise
