import argparse
import signal

from transcriber.ai_manager import RealAIManager
from transcriber.audio_transcriber import AudioTranscriber
from transcriber.clients.openai_clients import OpenAIAudioClient, OpenAIChatClient
from transcriber.config import TranscribeConfig
from transcriber.daemon import TranscriptionDaemon
from transcriber.logger import configure_logger, logger


def create_argument_parser() -> argparse.ArgumentParser:
    """Create the command-line interface without executing the application."""
    parser = argparse.ArgumentParser(
        description="Move, transcribe and summarize audio files into processed bundles.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate transcription and other jobs without actually writing changes.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging output.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one update cycle and exit instead of watching and serving continuously.",
    )
    return parser


def main() -> None:
    """Main entry point for the audio transcriber CLI.

    Parses command line arguments and runs the appropriate transcription mode.
    """
    args = create_argument_parser().parse_args()

    dry_run = args.dry_run
    debug = args.debug
    run_once = args.once

    configure_logger(debug)

    # global immutable configuration, loaded automatically by pydantic
    config = TranscribeConfig()  # pyright: ignore[reportCallIssue]

    # Create real clients
    audio_client = OpenAIAudioClient(config.audio)
    chat_client = OpenAIChatClient(config.text)

    # Inject clients into RealAIManager
    ai_manager = RealAIManager(
        audio_client=audio_client,
        chat_client=chat_client,
        config=config,
    )

    transcriber = AudioTranscriber(
        dry_run=dry_run,
        ai_manager=ai_manager,
        config=config,
    )
    if run_once:
        transcriber.run()
        return

    daemon = TranscriptionDaemon(transcriber, config)

    def shutdown(_signum: int, _frame: object) -> None:
        logger.info("Shutdown signal received")
        daemon.stop()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    daemon.run()
