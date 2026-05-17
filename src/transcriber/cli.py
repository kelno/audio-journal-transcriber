import argparse
import sys

from transcriber.ai_manager import AIManager
from transcriber.daemon import run_daemon_mode
from transcriber.exception import AudioTranscriberException

from .audio_transcriber import AudioTranscriber
from .logger import configure_logger, logger


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
    args = parser.parse_args()

    dry_run = args.dry_run
    debug = args.debug
    daemon = args.daemon

    configure_logger(debug)

    ai_manager = AIManager()
    transcriber = AudioTranscriber(dry_run=dry_run, ai_manager=ai_manager)
    try:
        unprocessed = transcriber.run()
        if daemon:
            run_daemon_mode(transcriber, unprocessed)
    except AudioTranscriberException as e:
        logger.error(f"AudioTranscriber failed with exception: {e}")
        sys.exit(1)
