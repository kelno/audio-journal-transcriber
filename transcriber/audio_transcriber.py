# pylint: disable=broad-exception-caught

from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin
from typing import List
import json
import os

import requests
from openai import OpenAI

from transcriber.config import TranscribeConfig
from transcriber.transcript_bundle import TranscriptBundle
from transcriber.logger import get_logger

OUTPUT_DIR_NAME = 'Output'
PENDING_DIR_NAME = "attachments"  # Directory where audio files are located

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
            - If the transcript mentions things to do, please highlight those todo as a recap at the end of the summary. (don't mention if none)
            - Given that this is a transcript that might be of poor quality, if you're confused or need to make assumptions, do mention it in your summary.
            {extra_context_prompt}
            Okay now here is the transcript:
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

    def process_audio_files(self, transcribing_dir: Path):
        """Process audio files from the pending directory."""

        pending_dir = transcribing_dir / PENDING_DIR_NAME
        output_dir = transcribing_dir / OUTPUT_DIR_NAME

        # Get list of audio files from pending directory

        logger.info(f"Looking for pending audio files in {pending_dir}")
        audio_files = self.find_pending_audio_files(pending_dir)

        if not audio_files:
            logger.info("No audio files found for processing")
            return

        logger.info(f"Found {len(audio_files)} audio files to process")

        for audio_path in audio_files:
            filename = os.path.basename(audio_path)
            rel_path = os.path.relpath(audio_path, pending_dir)
            # if rel_path == '.':
            #     rel_path = ''

            logger.info(f"Processing audio file: {filename} from {rel_path}")

            try:
                transcript = self.transcribe_audio(audio_path)
                summary = self.try_get_ai_summary(transcript)

                bundle = TranscriptBundle(source_audio=audio_path, transcript=transcript, ai_summary=summary)
                bundle.write(output_dir, dry_run=self.dry_run)

                self.remove_empty_directories(pending_dir)

                logger.info(f"Successfully processed: [{filename}] to bundle [{bundle.get_bundle_name()}]")

            except Exception as e:
                logger.error(f"Error processing [{filename}]:")
                raise e

    def log_section_header(self, message):
        """Log a section header with separators."""
        logger.info(f"========== {message} ==========")

    def create_subdirectories(self, transcribing_dir: Path):
        """
        Create 'Complete' and 'Errored' subdirectories if they don't exist.

        Args:
            transcribing_dir (str): Path to the transcribing directory
            logger: Logging object
        """
        complete_dir = os.path.join(transcribing_dir, 'Complete')
        errored_dir = os.path.join(transcribing_dir, 'Errored')

        for directory in [complete_dir, errored_dir]:
            if not os.path.exists(directory):
                os.makedirs(directory)
                logger.debug(f"Created directory: {directory}")

    @staticmethod
    def is_handled_audio_file(filename: str) -> bool:
        """
        Check if the file is an audio file based on extension.

        Args:
            filename (str): Name of the file

        Returns:
            bool: True if the file is an audio file, False otherwise
        """
        audio_extensions = ['.mp3', '.wav', '.m4a', '.flac', '.ogg', '.aac', '.mkv', '.mp4']
        return os.path.splitext(filename)[1].lower() in audio_extensions

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
                        text = result['text'] + ' '  # Add space after each chunk
                        text_chunks.append(text)
                        print(text, end='', flush=True)
                except Exception as e:
                    logger.error(f"Error decoding line:\n{line}")
                    raise e

        complete_transcript = ' '.join(text_chunks)
        # with open(output_text_path, 'w', encoding='utf-8') as f:
        #     f.write(complete_transcript)

        return complete_transcript


    def find_pending_audio_files(self, pending_dir: Path) -> List[Path]:
        """
        Find audio files in the given subdirectory and its subdirectories.
        Returns a list of Path objects for each audio file found.
        """
        if not pending_dir.exists():
            logger.info(f'No [{PENDING_DIR_NAME}] directory found')
            return []

        audio_files = []
        for path in pending_dir.rglob('*'):
            if path.is_file() and self.is_handled_audio_file(path.name):
                audio_files.append(path)
                logger.debug(f"Found audio file: [{path}]")

        return audio_files

    def remove_empty_directories(self, directory: Path):
        """Recursively remove directories inside given directory if they're empty."""
        try:
            # Walk bottom-up so we check deepest directories first
            for root, _dirs, _files in os.walk(directory, topdown=False):
                # Skip the root directory itself
                if root == directory:
                    continue

                if not os.listdir(root):  # Directory is empty (no files/subdirs)
                    logger.debug(f"Removing empty directory: {root}")
                    if not self.dry_run:
                        os.rmdir(root)

        except Exception as e:
            logger.warning(f"Error while removing empty directory {directory}: {e}")

    def cleanup_audio_files_older_than(self, transcribing_dir, days):
        """
        Clean up audio files that were processed more than X days ago.
        Only removes audio files that have a matching .md file next to them.
        Handles nested directory structure.
        """

        logger.info(f"Starting cleanup of audio files older than {days} days")
        complete_dir = os.path.join(transcribing_dir, OUTPUT_DIR_NAME)
        logger.debug(f"Scanning directory: {complete_dir}")

        current_time = datetime.now().timestamp()
        files_removed = 0
        files_checked = 0

        for root, _, files in os.walk(complete_dir):
            for filename in files:
                if not self.is_handled_audio_file(filename):
                    continue

                file_path = os.path.join(root, filename)
                files_checked += 1

                # Check for matching .md file
                base_name = os.path.splitext(filename)[0]
                md_path = os.path.join(root, f"{base_name}.md")

                if not os.path.exists(md_path):
                    logger.warning(f"Skipping [{filename}], as no matching text file was found. ")
                    continue

                mtime = os.path.getmtime(file_path)
                age_in_days = (current_time - mtime) / (24 * 3600)

                rel_path = os.path.relpath(root, complete_dir)
                logger.debug(f"\nChecking: \"{os.path.join(rel_path, filename)}\". "
                        f"Modified: {datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')}. "
                        f"Age: {int(age_in_days)} days.")

                if age_in_days > days:
                    logger.info(f"  Removing file: {file_path}")
                    if not self.dry_run:
                        os.remove(file_path)
                    files_removed += 1
                else:
                    logger.debug("  Keeping file (not old enough)")

        logger.info("Cleanup summary:")
        logger.info(f"  Files checked: {files_checked}")
        logger.info(f"  Files removed: {files_removed}")

    def run(self):
        transcribing_dir = self.obsidian_root / self.config.general.transcription_dir_path
        if not os.path.exists(transcribing_dir):
            raise ValueError(f"Transcribing directory does not exist: {transcribing_dir}")

        self.create_subdirectories(transcribing_dir)

        # Clean old audio files
        if self.config.general.cleanup != 0:
            self.log_section_header("Cleanup old audio files")
            self.cleanup_audio_files_older_than(transcribing_dir, self.config.general.cleanup)

        self.log_section_header("Processing Audio Files")
        self.process_audio_files(transcribing_dir)

        self.log_section_header("Summary")
        logger.info("Transcription process completed successfully.")
