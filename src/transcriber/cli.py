import argparse
import sys

from transcriber.ai_manager import AIManager
from transcriber.audio_transcriber import AudioTranscriber
from transcriber.clients.openai_clients import OpenAIAudioClient, OpenAIChatClient
from transcriber.config import TranscribeConfig
from transcriber.daemon import run_daemon_mode
from transcriber.exception import AudioTranscriberException
from transcriber.logger import configure_logger, logger


def main() -> None:
    """Main entry point for the audio transcriber CLI.

    Parses command line arguments and runs the appropriate transcription mode.
    """
    parser = argparse.ArgumentParser(
        description="Move, transcribe and summarize audio files into processed bundles.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate transcription and other jobs without actually writing changes. Will still create directories.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging output.",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run in daemon mode, watching file changes and processing continuously.",
    )
    parser.add_argument(
        "--one",
        action="store_true",
        help="Only process one bundle (for testing).",
    )
    args = parser.parse_args()

    dry_run = args.dry_run
    debug = args.debug
    daemon = args.daemon
    one_bundle = args.one

    configure_logger(debug)

    # global immutable configuration, loaded automatically by pydantic
    config = TranscribeConfig()  # pyright: ignore[reportCallIssue]

    # Create real clients
    audio_client = OpenAIAudioClient(config.audio)
    chat_client = OpenAIChatClient(config.text)

    # Inject clients into AIManager
    ai_manager = AIManager(
        audio_client=audio_client,
        chat_client=chat_client,
        config=config,
    )

    transcriber = AudioTranscriber(
        dry_run=dry_run,
        ai_manager=ai_manager,
        config=config,
        only_one_bundle=one_bundle,
    )
    try:
        unprocessed = transcriber.run()
        if daemon:
            run_daemon_mode(transcriber, unprocessed, config)
    except AudioTranscriberException:
        logger.exception("AudioTranscriber failed with exception")
        sys.exit(1)
