# pylint: disable=broad-exception-caught

from dataclasses import dataclass
import os
from pathlib import Path
import argparse
import shutil
from datetime import datetime
from urllib.parse import urljoin
from typing import List
import re
import json
import logging

import yaml
import coloredlogs
from pydantic import BaseModel, model_validator
import requests
from openai import OpenAI

from utils import ensure_directory_exists, touch_file

COMPLETE_DIR_NAME = 'Complete'
ERRORED_DIR_NAME = 'Errored'
PENDING_DIR_NAME = "attachments"  # Directory where audio files are located
# Regex to match YYYY-MM-DD format at the start of the filename
DATE_RE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}_') 

TMP_DIR_NAME = 'tmp'  # Temporary directory for processing files
LOG_FILE = "transcribe.log"

def init_logger() -> logging.Logger:
    """Initialize the logger with console and file handlers."""
    new_logger = logging.getLogger(__name__)
    coloredlogs.install(level='INFO', logger=new_logger, fmt='%(asctime)s,%(levelname)s: %(message)s', datefmt='%H:%M:%S')

    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
    new_logger.addHandler(file_handler)

    return new_logger

logger = init_logger()

# Configuration class matching yaml config
class TranscribeConfig(BaseModel):
    class GeneralConfig(BaseModel):
        transcription_dir_path: Path
        cleanup: int = 0

    class TextConfig(BaseModel):
        summary_enabled: bool
        api_base_url: str
        model: str
        api_key: str
        extra_context: str|None = None
    
        @model_validator(mode="after")
        def ensure_trailing_slash(cls, self): # pylint: disable=E0213
            if not self.api_base_url.endswith('/'):
                self.api_base_url += '/'
            return self
    
    class AudioConfig(BaseModel):
        api_base_url: str
        model: str
        api_key: str
        stream: bool = False

        @model_validator(mode="after")
        def ensure_trailing_slash(cls, self): # pylint: disable=E0213
            if not self.api_base_url.endswith('/'):
                self.api_base_url += '/'
            return self

    general: GeneralConfig
    text: TextConfig
    audio: AudioConfig

@dataclass
class AudioTranscriber:
    config: TranscribeConfig
    obsidian_root: Path
    dry_run: bool = False

    def __post_init__(self):
        self.obsidian_root = self.obsidian_root.resolve()
        if (self.dry_run):
            logger.warning("!!! DRY RUN MODE !!!")
        logger.info(
             f"{type(self).__name__} initialized with\n"
             f"Obsidian Root: {self.obsidian_root}\n"
             f"Transcribing dir: {self.config.general.transcription_dir_path}\n"
             f"Cleanup {self.config.general.cleanup}\n"
             f"Text summary {"enabled" if self.config.text.summary_enabled else "disabled"}")
        
        # Make sure obsidian_root exists
        if not os.path.exists(self.obsidian_root):
            raise ValueError(f"Obsidian root directory does not exist: {self.obsidian_root}")
            
    def transcribe_audio(self, audio_path: Path, output_text_path: Path):
        """
        Transcribe an audio file using a local OpenAI-compatible API with streaming.
        Writes output directly to file.
        """
        logger.info(f"Transcribing: {audio_path}")

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
            self.process_streaming_response(response, output_text_path)
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
     
    def get_ai_summary(self, transcript: str) -> str|None:
        """
        Queries configured LLM for a summary
        """

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

    def process_ai_summary(self, transcribe_path: Path):
        """
        Generate and append AI summary to the transcription file.
        """
        logger.debug(f"Appending AI summary to {transcribe_path}")
        try:
            with open(transcribe_path, "r", encoding="utf-8") as f:
                transcript = f.read()
                if summary := self.get_ai_summary(transcript):
                    logger.info(f"AI summary succeeded. Excerpt: {summary[:160]}...")  # Log first 100 chars
                    with open(transcribe_path, "a", encoding="utf-8") as f:
                        f.write("\n\n---\n\n# AI Summary\n\n")
                        f.write(summary)
        except Exception as e:
            logger.error(f"AI summary failed with exception: {e}")

    def process_audio_files(self, transcribing_dir: Path):
        """Process audio files from the pending directory."""

        pending_dir = transcribing_dir / PENDING_DIR_NAME
        complete_dir = transcribing_dir / COMPLETE_DIR_NAME
        errored_dir = transcribing_dir / ERRORED_DIR_NAME
        tmp_dir = transcribing_dir / TMP_DIR_NAME

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
                file_date = self.get_file_date_prefix(audio_path)
                audio_filename, text_filename = self.generate_output_filenames(filename, file_date)
                
                # Create subdirectories in Complete and temp processi ng directory
                temp_sub_dir = tmp_dir / rel_path
                complete_subdir = complete_dir / rel_path
                ensure_directory_exists(temp_sub_dir)
                ensure_directory_exists(complete_subdir)

                transcribed_file: Path = temp_sub_dir / text_filename
                logger.info(f"Will save transcription to: {transcribed_file}")

                # Transcribe audio directly to file
                if self.dry_run:
                    return  # for now

                self.transcribe_audio(audio_path, transcribed_file)
                if self.config.text.summary_enabled:
                    self.process_ai_summary(transcribed_file)

                self.move_to_complete(audio_path, transcribed_file, complete_subdir,
                            audio_filename, text_filename)
                self.remove_empty_directories(pending_dir)
                logger.info(f"Successfully processed: {filename}")

            except Exception as e:
                logger.error(f"Error processing {filename}: {e}")
                error_subdir = errored_dir / rel_path
                ensure_directory_exists(error_subdir)
                self.move_to_error(audio_path, transcribed_file, error_subdir, filename)

        # Cleanup temporary directory, should be empty after processing
        shutil.rmtree(tmp_dir)

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
        tmp_dir = os.path.join(transcribing_dir, TMP_DIR_NAME)

        for directory in [complete_dir, errored_dir, tmp_dir]:
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

    def process_streaming_response(self, response, output_text_path):
        """Process a streaming response from the transcription API and write directly to file."""
        logger.debug(f"Creating transcription file: {output_text_path}")
        with open(output_text_path, 'w', encoding='utf-8') as f:
            for line in response.iter_lines():
                if line:
                    try:
                        json_str = line.decode('utf-8').removeprefix('data: ')
                        if json_str.strip() == '[DONE]':
                            break
                        result = json.loads(json_str)
                        if 'text' in result:
                            text = result['text'] + ' '  # Add space after each chunk
                            f.write(text)
                            f.flush()
                            print(text, end='', flush=True)
                    except Exception as e:
                        logger.error(f"{output_text_path} Error decoding line:\n{line}\nException: {e}")
                        return False
        logger.info(f"\nCompleted writing transcription to {output_text_path}")


    def find_pending_audio_files(self, pending_dir: Path) -> List[Path]:
        """
        Find audio files in the given subdirectory and its subdirectories.
        Returns a list of tuples (source_path, relative_path).
        """
        if not os.path.exists(pending_dir):
            logger.info(f'No {PENDING_DIR_NAME} directory found')
            return []

        audio_files = []
        for root, _, files in os.walk(pending_dir):
            for filename in files:
                if self.is_handled_audio_file(filename):
                    full_path = os.path.join(root, filename)
                    audio_files.append(full_path)
                    logger.debug(f"Found audio file: {full_path}")

        return audio_files

    def get_file_date_prefix(self, audio_path: Path) -> str:
        """Get the file's date from its last modified time (format: YYYY-MM-DD).
        Falls back to current date if modification time is unavailable."""

        try:
            file_mtime = os.path.getmtime(audio_path)
            file_date = datetime.fromtimestamp(file_mtime).strftime('%Y-%m-%d')
            logger.info(f"Choosing date prefix: Using file's modified date: {file_date}")
            return file_date
        except OSError:
            file_date = datetime.now().strftime('%Y-%m-%d')
            logger.warning(f"Could not get file modification time, using current date: {file_date}")
            return file_date

    def generate_output_filenames(self, filename: str, file_date: str) -> tuple[str, str]:
        """Generate output filenames with date prefix if needed."""

        if not DATE_RE_PATTERN.match(filename):
            logger.debug(f"Adding date prefix to filename: {file_date}")
            audio_filename = f"{file_date}_{filename}"
            text_filename = f"{file_date}_{os.path.splitext(filename)[0]}.md"
        else:
            logger.debug("File already has date prefix")
            audio_filename = filename
            text_filename = f"{os.path.splitext(filename)[0]}.md"
            
        return audio_filename, text_filename

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
                    os.rmdir(root)
                    
        except Exception as e:
            logger.warning(f"Error while removing empty directory {directory}: {e}")
            
    def move_to_complete(self, audio_path: Path, text_path: Path, complete_dir: Path, 
                        audio_filename: str, text_filename: str):
        """Move processed files to Complete directory and update their modification times."""

        final_audio_path = complete_dir / audio_filename
        final_text_path = complete_dir / text_filename
        
        # Get source directory before moving files
        # source_dir = os.path.dirname(audio_path)
        
        logger.debug(f"Moving audio file to: {final_audio_path}")
        shutil.move(audio_path, final_audio_path)
        touch_file(final_audio_path)

        logger.debug(f"Moving transcription to: {final_text_path}")
        shutil.move(text_path, final_text_path)
        touch_file(final_text_path)

    def move_to_error(self, audio_path: Path, text_path: Path, errored_dir: Path, 
                    filename: str) -> None:
        """Move failed files to Errored directory."""

        error_path = errored_dir / filename
        logger.error(f"Failed to process {filename}. Moving to: {error_path}")
        shutil.move(audio_path, error_path)

        if os.path.exists(text_path):
            logger.error(f"Moving partial transcription to: {error_path}")
            shutil.move(text_path, error_path)

    def cleanup_audio_files_older_than(self, transcribing_dir, days):
        """
        Clean up audio files that were processed more than X days ago.
        Only removes audio files that have a matching .md file next to them.
        Handles nested directory structure.
        """

        logger.info(f"Starting cleanup of audio files older than {days} days")
        complete_dir = os.path.join(transcribing_dir, COMPLETE_DIR_NAME)
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
                    logger.info(f"Skipping \"{filename}\" - no matching .md file found")
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

        logger.info("CLEANUP SUMMARY:")
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

def load_config(config_dir) -> TranscribeConfig:
    yaml_path = os.path.join(config_dir, "config.yaml")
    with open(yaml_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
        
    return TranscribeConfig(**raw)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transcribe audio files in Obsidian Vault.")
    parser.add_argument("obsidian_root", type=str, help="The Obsidian root directory path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate transcription without actually processing audio files. Will still create directories.",
    )

    args = parser.parse_args()

    obsidian_root = Path(args.obsidian_root)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config = load_config(script_dir)
    
    AudioTranscriber(config=config, obsidian_root=obsidian_root, dry_run=args.dry_run).run()


# Improve me:
# - Move everything to a "Transcription" subdir, so that we can have our own structure inside and not depends on attachements
# - Change arguments? So we'd be given an input directory and the output where we place our results
# - Those file can be Obsidian notes and contain metadata on top such as the model used, the original file name, date of processing
# - Also we can store a "status" there to indicate success/failure and know what to retry, separate for audio & summary
# - Try grouping records being done very close to each other. Group them as a single result file basically.
# - Do a "remove empty audio" pass
# - Maybe use IA to name those recordings too, based on their content ?
# - Log runs in a file too?
#
# Future improvements:
# Maybe a way to provide context to the AI, such as a list of topics we care about, so that it can focus on those ? 
# get a list of recents notes titles or tasks to give to it.
