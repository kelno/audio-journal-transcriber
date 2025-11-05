from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin
from typing import List
import json
import os

import requests
from openai import OpenAI

from transcriber.config import TranscribeConfig
from transcriber.globals import is_handled_audio_file
from transcriber.transcript_bundle import TranscriptBundle
from transcriber.logger import get_logger
from transcriber.transcribe_job import TranscribeJob
from transcriber.utils import ensure_directory_exists, remove_empty_subdirs

OUTPUT_DIR_NAME = 'Output'
SOURCE_DIR_NAME = "attachments"  # Directory where audio files are located

logger = get_logger()

@dataclass
class AudioTranscriber:
    config: TranscribeConfig
    obsidian_root: Path
    dry_run: bool = False

    def __post_init__(self):
        self.obsidian_root = self.obsidian_root.resolve()
        if self.dry_run:
            logger.warning("!!! DRY RUN MODE !!!")
        logger.info(
             f"{type(self).__name__} initialized with\n"
             f"Obsidian Root: {self.obsidian_root}\n"
             f"Transcribing dir: {self.config.general.transcription_dir_path}\n"
             f"Cleanup {self.config.general.cleanup}\n"
             f"Text summary {"enabled" if self.config.text.summary_enabled else "disabled"}")

        # Make sure obsidian_root exists
        if not self.obsidian_root.exists():
            raise ValueError(f"Obsidian root directory does not exist: {self.obsidian_root}")

    def transcribe_audio(self, audio_path: Path) -> str:
        """
        Transcribe an audio file using a local OpenAI-compatible API with streaming.
        Writes output directly to file.
        """
        logger.info(f"Transcribing: {audio_path}")
        if self.dry_run:
            return "(dry run transcript)"

        with open(audio_path, 'rb') as audio_file:
            files = {
                'file': (os.path.basename(audio_path), audio_file, 'multipart/form-data')
            }
            data = {
                'model': self.config.audio.model,
                'stream': "true" if self.config.audio.stream else "false",
            }
            url = urljoin(self.config.audio.api_base_url, "audio/transcriptions")
            response = requests.post(
                url=url,
                files=files,
                data=data,
                headers={'Authorization': f'Bearer {self.config.audio.api_key}'},
                stream=True,
                timeout=60 if self.config.audio.stream else 600 # 1 min for streaming, 10 min for non-streaming
            )

        if response.status_code == 200:
            return self.extract_streaming_response(response)
        else:
            raise ValueError(f"Transcription failed with status code {response.status_code} and response: {response.text}")

    def query_chat_completion(self, prompt: str) -> str:
        """
        Returns the output string on success, or None on failure.
        Raises:
            *: Pass through any exceptions from the OpenAI client.
        """

        client = OpenAI(base_url="http://localhost:8080/api/", api_key=self.config.text.api_key)

        # https://platform.openai.com/docs/api-reference/chat/create
        completion = client.chat.completions.create(
            model=self.config.text.model,
            messages=[
                {"role": "system", "content": "You are part of an automated pipeline to transcribe and summarize texts."},
                {"role": "user", "content": prompt}
            ]
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
        Queries the configured LLM for a summary
        """
        if self.dry_run:
            return "(dry run summary)"

        extra_context_prompt = f"Extra context:\n{self.config.text.extra_context}" if self.config.text.extra_context is not None else ""
        prompt = f"""
            You are part of an automated pipeline to transcribe and summarize texts. 
            Please summarize the following transcript of my own audio recording. 
            Extra instructions:
            - Use the input language for the summary.
            - You should act like a function and only output the summary.
            - If and only if the transcript very specifically mentions things that should be done, highlight those points as a recap at the end of the summary with the title "Action Items".
            - Given that this is an audio transcript that might be of poor quality, you might need to make some assumptions as to what was said. In those instances, take extra care to announce your assumptions clearly.
            {extra_context_prompt}
            Okay. Now the transcript follows:
            ---
            {transcript}"""
        return self.query_chat_completion(prompt)

    def try_get_ai_summary(self, transcript: str) -> str:
        """
        Generate and append AI summary to the transcription file.
        """
        if not self.config.text.summary_enabled:
            return "(summary is disabled in config)"

        logger.debug("Generating AI summary")

        try:
            summary = self.get_ai_summary(transcript)
            logger.info(f"AI summary succeeded. Excerpt: {summary[:160]}...")  # Log first 100 chars
            return summary
        except Exception as e:
            logger.error(f"AI summary failed with exception: {e}")
            raise

    def process_jobs(self, jobs, output_dir: Path):
        """Process audio files from the pending directory."""

        for job in jobs:
            audio_path: Path = job.audio_file
            filename = os.path.basename(audio_path)

            logger.info(f"Processing audio file: {audio_path}")

            try:
                transcript = self.transcribe_audio(audio_path)
                summary = self.try_get_ai_summary(transcript)

                bundle = TranscriptBundle(config=self.config, source_audio=audio_path, transcript=transcript, ai_summary=summary)
                bundle.write(output_dir, dry_run=self.dry_run)

                logger.info(f"Successfully processed: [{filename}] to bundle [{bundle.get_bundle_name()}]")

            except Exception as e:
                logger.error(f"Error processing [{filename}]:")
                raise e

    def log_section_header(self, message):
        """Log a section header with separators."""
        logger.info(f"========== {message} ==========")

    def extract_streaming_response(self, response) -> str:
        """Process a streaming response from the transcription API and write directly to file.
        Returns the complete transcript as a string."""
        logger.debug("Processing streaming response")
        text_chunks = []

        for line in response.iter_lines():
            if line:
                try:
                    json_str = line.decode('utf-8').removeprefix('data: ')
                    if json_str.strip() == '[DONE]':
                        break
                    result = json.loads(json_str)
                    if 'text' in result:
                        text = result['text']
                        text_chunks.append(text)
                        print(text, end='', flush=True)
                except Exception as e:
                    logger.error(f"Error decoding line:\n{line}")
                    raise e

        print("\n")
        complete_transcript = ' '.join(text_chunks)

        return complete_transcript

    def gather_jobs(self, source_dir: Path) -> List[TranscribeJob]:
        """
        Find audio files in the given subdirectory and its subdirectories.
        Returns a list of Path objects for each audio file found.
        """
        if not source_dir.exists():
            raise FileNotFoundError(f'Source directory does not exist: {source_dir}')

        logger.debug(f"Looking for pending jobs in {source_dir}")

        jobs = []
        for path in source_dir.rglob('*'):
            if path.is_file() and is_handled_audio_file(path.name):
                jobs.append(TranscribeJob(audio_file=path))
                logger.debug(f"Found audio file: [{path}]")

        return jobs

    def run(self):
        transcribing_dir = self.obsidian_root / self.config.general.transcription_dir_path
        if not os.path.exists(transcribing_dir):
            raise ValueError(f"Transcribing directory does not exist: {transcribing_dir}")

        output_dir = transcribing_dir / OUTPUT_DIR_NAME

        # Clean old audio files
        if self.config.general.cleanup != 0:
            self.log_section_header("Cleanup old audio files")
            TranscriptBundle.cleanup_audio_files_older_than(output_dir, self.config.general.cleanup, self.dry_run)

        self.log_section_header("Processing Audio Files")

        source_dir = transcribing_dir / SOURCE_DIR_NAME

        self.log_section_header("Gathering Jobs")
        jobs = self.gather_jobs(source_dir)
        if not jobs:
            logger.info("No jobs found for processing")
        else:
            self.log_section_header("Processing Jobs")
            ensure_directory_exists(output_dir)
            self.process_jobs(jobs, output_dir)

        if not self.dry_run:
            remove_empty_subdirs(source_dir)

        self.log_section_header("Summary")
        logger.info("Transcription process completed successfully.")
